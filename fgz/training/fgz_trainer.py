from datetime import datetime
import os
from warnings import warn
import minerl
import gym
import numpy as np
import torch
import torch.nn.functional as F
import wandb
import cv2

from tqdm import tqdm

from fractal_zero.search.fmc import FMC
from fractal_zero.data.tree_sampler import TreeSampler

from vpt.agent import MineRLAgent
from fgz.architecture.dynamics_function import DynamicsFunction

from fgz.data_utils.data_handler import DataHandler
from fgz.loading import get_agent
from fgz_config import FGZConfig, TASKS


class FGZTrainer:
    def __init__(
        self,
        agent: MineRLAgent,
        fmc: FMC,
        data_handler: DataHandler,
        dynamics_function_optimizer: torch.optim.Optimizer,
        config: FGZConfig,
    ):
        if config.disable_fmc_detection:
            assert len(config.enabled_tasks) >= 2
        else:
            assert len(config.enabled_tasks) >= 1

        self.agent = agent
        self.data_handler = data_handler
        self.current_trajectory_window = None

        # TODO: create dynamics function externally as the vectorized environment inside FMC.
        # self.dynamics_function = DynamicsFunction(
        #     state_embedding_size=state_embedding_size,
        #     discriminator_classes=2,
        # )
        self.fmc = fmc
        self.config = config

        self.fmc.vec_env.dynamics_function = self.fmc.vec_env.dynamics_function.to(agent.device)
        self.dynamics_function_optimizer = dynamics_function_optimizer

        self.train_steps_taken = 0

        if self.config.use_wandb:
            self.run_name = wandb.run.name
        else:
            self.run_name = datetime.now().strftime("%Y-%m-%d_%I-%M-%S_%p")

    @property
    def dynamics_function(self):
        return self.fmc.vec_env.dynamics_function

    @property
    def num_tasks(self):
        return self.data_handler.num_tasks

    def get_fmc_trajectory(self, root_embedding):
        # ensure FMC is exploiting the correct logit. this basically means FMC will try to
        # find actions that maximize the discriminator's confusion for this specifc task.
        # the hope is that FMC will choose actions that make the discriminator beieve they are
        # from the expert performing that task.
        self.fmc.vec_env.set_target_logit(self.current_trajectory_window.task_id)

        self.fmc.vec_env.set_all_states(root_embedding.squeeze())
        self.fmc.reset()

        self.fmc.simulate(self.config.fmc_steps)

        self.tree_sampler = TreeSampler(self.fmc.tree, sample_type="best_path")
        observations, actions, _, confusions, infos = self.tree_sampler.get_batch()
        assert len(observations) == len(actions) == len(confusions) == len(infos)

        self.fmc_actions = actions
        self.fmc_observations = observations
        self.fmc_discrim_logits = infos
        self.fmc_confusions = torch.tensor(confusions[1:], requires_grad=False)

        self.fmc_average_reward = self.fmc_confusions.mean()

        return self.fmc_discrim_logits

    def _log_accuracies(self, logits, targets: torch.Tensor, task_targets: torch.Tensor=None):
        if task_targets is None:
            task_targets = targets

        preds = logits.argmax(1)
        self.expert_correct_logit_count += (preds == targets).sum()

        if not self.config.disable_fmc_detection:
            task_preds = logits[:, :-1].argmax(1)
            self.expert_correct_task_count += (task_preds == task_targets).sum()

        self.current_trajectory_processed_frame_count += targets.numel()

    def get_fmc_loss(self, full_window):
        fmc_root_embedding = full_window[0][1]
        discrim_logits = self.get_fmc_trajectory(fmc_root_embedding)

        discrim_logits = discrim_logits[1:]
        self.fmc_steps_taken = len(discrim_logits)
    
        if self.fmc_steps_taken <= 0:
            warn("Uh oh... FMC steps was <= 0.")
            return 0  # TODO: this should never happen.

        # TODO: explain
        discrim_logits = [logits.unsqueeze(0) for logits in discrim_logits]
        discrim_logits = torch.cat(discrim_logits)

        device = discrim_logits.device

        # TODO: should we make this a distribution where the target logit for the task
        # is also slightly higher?
        fmc_discriminator_targets = (
            torch.ones(self.fmc_steps_taken, dtype=torch.long, device=device) * self.config.fmc_logit
        )

        # fmc_confusions = [c.unsqueeze(0) for c in fmc_confusions[1:]]
        # fmc_confusions = torch.cat(fmc_confusions)
        # fmc_discriminator_targets = torch.zeros_like(fmc_confusions)

        # self.fmc_steps_taken = len(fmc_confusions)
        # return F.mse_loss(fmc_confusions, fmc_discriminator_targets)

        # TODO: add the correct task accuracies in here as well.

        task_targets = torch.tensor(
            [self.current_trajectory_window.task_id] * self.fmc_steps_taken,
            dtype=torch.long,
            device=device,
        )

        self._log_accuracies(discrim_logits, fmc_discriminator_targets, task_targets)

        return F.cross_entropy(discrim_logits, fmc_discriminator_targets)

    def get_expert_loss(self, full_window):
        _, root_embedding, _ = full_window[0]

        # each logit corresponds to one of the tasks. we can consider this to be our label
        target_logit = torch.tensor(
            [self.current_trajectory_window.task_id],
            dtype=torch.long,
            device=root_embedding.device,
        )

        # one-hot encode the task classificaiton target
        # classification_target = torch.zeros(self.num_tasks, dtype=torch.bool)
        # classification_target[target_logit] = 1

        # unroll expert
        embedding = root_embedding.squeeze(0)
        loss = 0.0

        self.expert_consistency_loss = 0.0

        # make sure we unroll to the length of FMC
        c = 0
        for i in range(self.config.unroll_steps):
            _, _, action = full_window[i]
            embedding, logits = self.dynamics_function.forward_action(embedding, action)
            loss += F.cross_entropy(logits, target_logit)

            self._log_accuracies(logits, target_logit)

            # squared error (later it's averaged so MSE.)
            if i < len(full_window) - 1:
                _, expected_embedding, _ = full_window[i + 1]  # use next embedding
                self.expert_consistency_loss += torch.mean(
                    (embedding - expected_embedding) ** 2
                )
                c += 1

        self.expert_consistency_loss /= c

        # percent = correct.sum() / len(correct)
        # print("accuracy", percent)
        # print("expert accuracy:", self.expert_correct_frame_count / self.expert_total_frame_count)

        return loss / self.config.unroll_steps

    def _get_tqdm_description(self) -> str:
        task_name = TASKS[self.current_trajectory_window.task_id]["name"]
        uid = self.current_trajectory_window.uid[-8:]
        start_frame = self.current_trajectory_window.start
        end_frame = self.current_trajectory_window.end
        return f"Training on {task_name}({uid})[{start_frame}:{end_frame}]"

    def get_loss_for_sub_trajectory(
        self, max_steps: int = None, use_tqdm: bool = False
    ):
        if self.config.disable_fmc_detection:
            self.fmc_steps_taken = 0
            self.fmc_confusions = np.zeros(1)

        # reset the hidden state of the agent, so we don't carry over any context from
        # the previous trajectory.
        self.agent.reset()
        self.current_trajectory_window = self.data_handler.sample_single_trajectory()

        desc = self._get_tqdm_description()
        it = tqdm(
            self.current_trajectory_window,
            desc=desc,
            disable=not use_tqdm,
            total=max_steps,
        )

        total_loss = 0.0
        total_consistency_loss = 0.0

        step = 0  # just in case.
        for step, full_window in enumerate(it):
            # TODO: maybe we can implement self-consistency loss like the EfficientZero paper?

            if self.config.disable_fmc_detection:
                fmc_loss = 0.0
            else:
                fmc_loss = self.get_fmc_loss(full_window)

            expert_loss = self.get_expert_loss(full_window)

            loss = expert_loss + fmc_loss

            # TODO: should we backprop after the full trajectory's losses have been calculated? or should we do it each window?
            # loss.backward()
            # self.dynamics_function_optimizer.step()
            total_loss += loss
            total_consistency_loss += self.expert_consistency_loss

            if max_steps and step >= max_steps:
                break

        total_loss /= step + 1
        total_consistency_loss /= step + 1
        # total_consistency_loss *= 0.001

        classification_loss = total_loss

        # NOTE: this coefficient can be 0.
        total_loss += total_consistency_loss * self.config.consistency_loss_coeff

        return total_loss, total_consistency_loss, classification_loss

    def train_sub_trajectories(
        self, batch_size: int, use_tqdm: bool = False, max_steps: int = None
    ):
        """
        2 trajectories are gathered:
            1.  From expert dataset, where the entire trajectory is lazily loaded as a contiguous piece.
            2.  Each state in the expert trajectory is embedded using a pretrained agent model. FMC runs a simulation for
                every embedded state from the expert trajectory/agent while trying to maximize a specific discriminator
                logit (to confuse the discriminator into believing it's actions came from the expert dataset).

        Then, the discriminator is trained to recognize the differences between true expert trajectories and trajectories
        resulting from exploiting the discriminator's confusion.
        """

        self.dynamics_function_optimizer.zero_grad()

        # for accuracy calc
        self.expert_correct_logit_count = 0
        self.expert_correct_task_count = 0
        self.current_trajectory_processed_frame_count = 0

        task_ids = []

        total_loss = 0.0
        total_consistency_loss = 0.0
        total_classification_loss = 0.0

        if self.config.disable_fmc_detection and batch_size % 2 != 0:
            raise ValueError("Batch size should be an even number when enabling FMC detection.")
            
        num_batch_steps = batch_size if self.config.disable_fmc_detection else batch_size // 2

        for _ in range(num_batch_steps):
            (
                loss,
                consistency_loss,
                classification_loss,
            ) = self.get_loss_for_sub_trajectory(max_steps=max_steps, use_tqdm=use_tqdm)

            task_ids.append(self.current_trajectory_window.task_id)

            total_loss += loss
            total_consistency_loss += consistency_loss
            total_classification_loss += classification_loss

        total_loss /= batch_size
        total_consistency_loss /= batch_size
        total_classification_loss /= batch_size

        logit_accuracy = (
            self.expert_correct_logit_count / self.current_trajectory_processed_frame_count
        )

        include_consistency_loss = False
        if include_consistency_loss:
            total_loss = (total_loss + total_consistency_loss) / 2

        total_loss.backward()
        self.dynamics_function_optimizer.step()

        if self.config.use_wandb and wandb.run:
            task_accuracy = self.expert_correct_task_count / self.current_trajectory_processed_frame_count
            
            if self.config.disable_fmc_detection:
                self.fmc_average_reward = 0
            
            wandb.log(
                {
                    # "train/fmc_loss": fmc_loss,
                    # "train/expert_loss": expert_loss,
                    "train/total_loss": total_loss,
                    "train/expert_consistency_loss": total_consistency_loss,
                    "train/classification_loss": total_classification_loss,
                    "train/accuracy": logit_accuracy,
                    "train/no_fmc_task_accuracy": task_accuracy,
                    "metrics/frame_count": self.current_trajectory_processed_frame_count,
                    "fmc/steps_taken": self.fmc_steps_taken,
                    "fmc/average_reward": self.fmc_average_reward,
                    # "fmc/average_confusion_reward": self.fmc_confusions.mean(),
                }
            )

        if self.config.verbose:
            print("\n\n-------------------")
            print("batch task ids:", task_ids)
            print(
                "loss:",
                total_loss.item(),
                "classification_loss:",
                total_classification_loss.item(),
                "consistency loss:",
                total_consistency_loss.item(),
            )
            print("accuracy:", logit_accuracy)

        # unroller = ExpertDatasetUnroller(self.agent, window_size=self.unroll_steps + 1)
        # for expert_sequence in unroller:
        #     start_embedding, _ = expert_sequence[0]  # "representation function"

        #     fmc_dynamics_embeddings, fmc_actions = self.get_fmc_trajectory(
        #         start_embedding,
        #         max_steps=self.unroll_steps
        #     )
        #     expert_embeddings, expert_actions = unroller.decompose_window(expert_sequence[1:])

        self.train_steps_taken += 1

        # return logit_accuracy
        return -total_loss

    @torch.no_grad()
    def evaluate(
        self,
        minerl_environment_id: str,
        render: bool = False,
        save_video: bool = False,
        max_steps: int = None,
        force_no_escape: bool = False,
    ):
        target_logit = self.config.environment_id_to_task_logit[minerl_environment_id]

        env = gym.make(minerl_environment_id)
        obs = env.reset()

        self.eval_actions = []
        self.eval_rewards = []

        self.agent.reset()
        self.fmc.vec_env.set_target_logit(target_logit)

        # TODO: manage FMC device more carefully.
        # self.fmc.vec_env.dynamics_function.to(torch.device("cpu"))

        video_path = None
        if save_video:
            video_path = f"./train/checkpoints/{self.run_name}/eval_{self.train_steps_taken}_{minerl_environment_id}.mp4"
            video_recorder = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), 20, (640, 360))

        step = 0
        while True:
            embedding = self.agent.forward_observation(obs, return_embedding=True)
            self.fmc.vec_env.set_all_states(embedding.squeeze())
            self.fmc.reset()
            # self.fmc.simulate(self.config.unroll_steps)
            self.fmc.simulate(self.config.fmc_steps)

            # get best FMC action
            path = self.fmc.tree.best_path
            action = path.get_action_between(
                path.ordered_states[0], path.ordered_states[1]
            )

            if force_no_escape:
                action["ESC"] = np.zeros(1, dtype=int)

            self.eval_actions.append(action)

            obs, reward, done, info = env.step(action)
            self.eval_rewards.append(reward)

            print(f"step {step} average path reward:", path.average_reward)

            if render:
                env.render()

            if save_video:
                video_recorder.write(obs["pov"][..., ::-1])

            if max_steps is not None:
                if step >= max_steps:
                    break

            if done:
                break

            step += 1

        env.close()
        
        if save_video:
            return video_path

    def save(self, directory: str = "./train", filename: str = None):
        current_trajectory_window = self.current_trajectory_window
        agent = self.agent
        data_handler = self.data_handler

        # these values cannot be saved for some reason.
        self.current_trajectory_window = None
        self.agent = None
        self.data_handler.agent = None
        self.fmc.vec_env.agent = None

        if filename is None:
            folder = os.path.join(directory, f"{self.run_name}")
            os.makedirs(folder, exist_ok=True)
            filename = os.path.join(folder, f"step_{self.train_steps_taken}.pth")

        torch.save(self, filename)

        self.current_trajectory_window = current_trajectory_window
        self.agent = agent
        self.data_handler = data_handler
        self.data_handler.agent = agent
        self.fmc.vec_env.agent = agent

        return filename

    @staticmethod
    def load(path: str, device=None):
        trainer: FGZTrainer = torch.load(path)

        config = trainer.config
        print(f"Loading with config: {config}")

        agent = get_agent(config)

        if device is not None:
            agent = agent.to(device)
            trainer.fmc.vec_env.dynamics_function = trainer.fmc.vec_env.dynamics_function.to(device)

        trainer.agent = agent
        trainer.data_handler.agent = agent
        trainer.fmc.vec_env.agent = agent
        return trainer
