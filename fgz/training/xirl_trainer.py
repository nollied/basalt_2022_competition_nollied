
from re import I
from typing import List
import ray
from vpt.agent import MineRLAgent
from fgz.architecture.dynamics_function import DynamicsFunction

from fgz.data_utils.xirl_data import XIRLDataHandler
from vpt.run_agent import load_agent

import torch


# @ray.remote
class XIRLTrainer:

    def __init__(
        self, 
        dataset_path: str, 
        model_path: str, 
        weights_path: str
    ):

        # NOTE: we can't use the same agent without more complicated thread-safeness code.
        self.agent = load_agent(model_path, weights_path)

        self.dynamics_function = DynamicsFunction(embedder_layers=2)

        self.optimizer = torch.optim.Adam(self.dynamics_function.parameters(), lr=0.01)

        self.data_handler = XIRLDataHandler(dataset_path, self.agent, self.dynamics_function)

    def soft_nearest_neighbor(self, frame_embedding: torch.Tensor, other_embeddings: List[torch.Tensor], return_similarity_vector: bool):
        # TODO: optimize this!
        soft_v = 0

        # similarity_vector = torch.zeros(size=(len(other_embeddings),), dtype=float)

        expanded_frame_embedding = frame_embedding.unsqueeze(0).expand(len(other_embeddings), -1)

        exp_norm = torch.exp(-torch.norm(expanded_frame_embedding - other_embeddings, dim=1))
        alpha_k = exp_norm / exp_norm.sum()

        if return_similarity_vector:
            return alpha_k

        return torch.matmul(alpha_k, other_embeddings)

    def train_on_pair(self, num_frame_samples: int=20):
        self.t0, self.t1 = self.data_handler.sample_pair()
        # self.t0 = torch.ones((10, 5))
        # self.t1 = torch.ones((10, 5))

        print("bytes for the pair of trajectories:", self.get_nbytes_stored())

        self.dynamics_function.train()
        self.optimizer.zero_grad()

        total_loss = 0
        for _ in range(num_frame_samples):

            # pick random frame in t0
            chosen_frame_index = torch.randint(low=0, high=len(self.t0), size=(1,)).item()
            ui = self.t0[chosen_frame_index]

            # calculate cycle MSE loss
            v_squiggly = self.soft_nearest_neighbor(ui, self.t1, return_similarity_vector=False)
            beta = self.soft_nearest_neighbor(v_squiggly, self.t0, return_similarity_vector=True)

            # TODO: vectorize
            frame_mult = torch.arange(start=1, end=len(beta) + 1, dtype=float).float() / len(beta)
            mu = torch.matmul(frame_mult, beta)

            # divide both by total num frames to make indices in more reasonable range
            # mu /= total_frames
            t = chosen_frame_index / len(beta)
            print(mu, t, len(beta))

            loss = ((mu - t) ** 2) / len(beta)

            print(loss.item())
            total_loss += loss

        loss = total_loss / num_frame_samples
        print("avg loss", loss.item())
        loss.backward()
        self.optimizer.step()

    def get_nbytes_stored(self):
        nbytes0 = sum([e.nelement() * e.element_size() for e in self.t0])
        nbytes1 = sum([e.nelement() * e.element_size() for e in self.t1])
        return nbytes0 + nbytes1

