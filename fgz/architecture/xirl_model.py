
from vpt.agent import MineRLAgent
from xirl_zero.architecture.dynamics_function import DynamicsFunction

import torch
from xirl_config import XIRLConfig

from vpt.run_agent import load_agent


class XIRLModel(torch.nn.Module):

    def __init__(self, config: XIRLConfig, device=None):
        super().__init__()

        self.config = config

        agent = load_agent(config.model_path, config.weights_path, device=device)

        self.img_preprocess = agent.policy.net.img_preprocess
        self.img_process = agent.policy.net.img_process

    def embed(self, frames):
        if frames.dim() == 3:
            frames = frames.unsqueeze(0)
        elif frames.dim() != 4:
            raise NotImplementedError(frames.shape)

        x = self.img_preprocess(frames).unsqueeze(0)  # ficticious time-dimension
        x = self.img_process(x)
        x = x[0]  # remove time dim
        return x