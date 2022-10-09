import minerl
import gym
import torch
import torch.nn.functional as F

from tqdm import tqdm

from fractal_zero.search.fmc import FMC
from fractal_zero.data.tree_sampler import TreeSampler

from vpt.agent import MineRLAgent
from fgz.architecture.dynamics_function import DynamicsFunction

from fgz.data_utils.data_handler import DataHandler


# TODO: move to train script
FMC_LOGIT = 4 


class FGZTrainer:

    def __init__(
        self,
        minerl_env: gym.Env,
        agent: MineRLAgent,
        fmc: FMC,
        data_handler: DataHandler,
        dynamics_function_optimizer: torch.optim.Optimizer,
        unroll_steps: int=8,
    ):
        self.minerl_env = minerl_env
        self.agent = agent
        self.data_handler = data_handler

        # TODO: create dynamics function externally as the vectorized environment inside FMC.
        # self.dynamics_function = DynamicsFunction(
        #     state_embedding_size=state_embedding_size,
        #     discriminator_classes=2,
        # )
        self.fmc = fmc
        self.unroll_steps = unroll_steps

        self.dynamics_function_optimizer = dynamics_function_optimizer

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

        self.fmc.simulate(self.unroll_steps)

        self.tree_sampler = TreeSampler(self.fmc.tree, sample_type="best_path")
        observations, actions, _, confusions, infos = self.tree_sampler.get_batch()
        assert len(observations) == len(actions) == len(confusions) == len(infos)
        return observations, actions, infos # confusions

    def get_fmc_loss(self, full_window):
        fmc_root_embedding = full_window[0][1]
        fmc_obs, fmc_acts, discrim_logits = self.get_fmc_trajectory(fmc_root_embedding)

        discrim_logits = discrim_logits[1:]
        self.fmc_steps_taken = len(discrim_logits)

        # TODO: explain
        discrim_logits = [logits.unsqueeze(0) for logits in discrim_logits]
        discrim_logits = torch.cat(discrim_logits)
        fmc_discriminator_targets = torch.ones(self.fmc_steps_taken, dtype=torch.long) * FMC_LOGIT

        # fmc_confusions = [c.unsqueeze(0) for c in fmc_confusions[1:]]
        # fmc_confusions = torch.cat(fmc_confusions)
        # fmc_discriminator_targets = torch.zeros_like(fmc_confusions)

        # self.fmc_steps_taken = len(fmc_confusions)
        # return F.mse_loss(fmc_confusions, fmc_discriminator_targets)

        print(discrim_logits.shape)
        print(fmc_discriminator_targets.shape)

        return F.cross_entropy(discrim_logits, fmc_discriminator_targets)

    def get_expert_loss(self, full_window):
        frame, root_embedding, action = full_window[0]
        dynamics: DynamicsFunction = self.fmc.vec_env.dynamics_function

        # each logit corresponds to one of the tasks. we can consider this to be our label
        target_logit = torch.tensor([self.current_trajectory_window.task_id], dtype=torch.long)

        # one-hot encode the task classificaiton target
        # classification_target = torch.zeros(self.num_tasks, dtype=torch.bool)
        # classification_target[target_logit] = 1

        # unroll expert
        embedding = root_embedding.squeeze(0)
        loss = 0.0

        # make sure we unroll to the length of FMC
        for _ in range(self.fmc_steps_taken):
            embedding, logits = dynamics.forward_action(embedding, action)

            loss += F.cross_entropy(logits, target_logit)

        # for frame, state_embedding, action in full_window:
            # NOTE: FMC may decide not to go the full depth, meaning it could return observations/actions that are
            # not as long as the original window. for that case, we will shorten the window here.
            # steps = len(fmc_obs)
            # window = full_window[:steps]
            # assert len(window) == len(fmc_obs)

            # NOTE: the first value in the confusions list will be 0 or None, so we should ignore it.
            # this is because the first value corresponds to the root of the search tree (which doesn't
            # have a reward). 
            # break
        return loss / self.fmc_steps_taken

    def train_trajectory(self, use_tqdm: bool=False):
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

        self.current_trajectory_window = self.data_handler.sample_single_trajectory()
        
        it = tqdm(self.current_trajectory_window, desc="Training on Trajectory", disable=not use_tqdm)
        for full_window in it:
            
            fmc_loss = self.get_fmc_loss(full_window)

            expert_loss = self.get_expert_loss(full_window)

            print(fmc_loss, expert_loss)
            loss = fmc_loss + expert_loss

            # TODO: should we backprop after the full trajectory's losses have been calculated? or should we do it each window?
            loss.backward()
            self.dynamics_function_optimizer.step()
            break

        # unroller = ExpertDatasetUnroller(self.agent, window_size=self.unroll_steps + 1)
        # for expert_sequence in unroller:
        #     start_embedding, _ = expert_sequence[0]  # "representation function"

        #     fmc_dynamics_embeddings, fmc_actions = self.get_fmc_trajectory(
        #         start_embedding, 
        #         max_steps=self.unroll_steps
        #     )
        #     expert_embeddings, expert_actions = unroller.decompose_window(expert_sequence[1:])
