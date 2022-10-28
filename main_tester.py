import os

import gym

import torch

from xirl_zero.main_trainer import Trainer
from xirl_zero.architecture.dynamics_function import MineRLDynamicsEnvironment


class Tester:
    env: gym.Env = None

    def __init__(self, path_to_experiment: str, iteration: int=None, device=None):
        checkpoint_dir = os.path.join(path_to_experiment, "checkpoints")

        # default pick the last iteration
        if iteration is None:
            iterations = [int(fn.split(".")[0]) for fn in os.listdir(checkpoint_dir)]
            iteration = max(iterations)
        
        fn = f"{iteration}.pth"
        checkpoint_path = os.path.join(checkpoint_dir, fn)
        target_state_path = os.path.join(path_to_experiment, "target_states", fn)

        print(f"Loading {fn} checkpoint and target state from {path_to_experiment}")

        trainer: Trainer = torch.load(checkpoint_path, map_location=device)
        target_state: torch.Tensor = torch.load(target_state_path, map_location=device)

        self.minerl_env_id = trainer.config.minerl_env_id

        self.representation_function = trainer.representation_trainer.model
        self.dynamics_function = trainer.dynamics_trainer.model
        self.target_state = target_state

    def load_environment(self, env: gym.Env=None):
        if env is None:
            env = gym.make(self.minerl_env_id)

        actual_env_id = env.unwrapped.spec.id
        if actual_env_id != self.minerl_env_id:
            raise ValueError(f"Cross-task testing is not recommended. The actual env ID loaded was {actual_env_id}, but we expected {self.minerl_env_id}.")

        self.env = env
        # self.dynamics_env = MineRLDynamicsEnvironment(self.env.action_space, self.dynamics_function)


if __name__ == "__main__":
    tester = Tester("./train/xirl_zero/2022-10-28_02-41-38_PM")
    tester.load_environment()
