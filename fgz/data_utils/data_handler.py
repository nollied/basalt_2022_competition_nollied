
import os
from glob import glob

import minerl
from typing import List

from fgz.data_utils.data_loader import trajectory_generator
import torch

import numpy as np


class ContiguousTrajectory:
    """Note: when iterating this class, only 1 frame will be yielded at a time. 
    For a window/batch, use `ContiguousTrajectoryWindow`.
    """

    def __init__(self, video_path: str, json_path: str, uid: str, task_id: int):
        self.video_path = video_path
        self.json_path = json_path
        self.uid = uid
        self.task_id = task_id

    def __str__(self) -> str:
        return f"T({self.uid})"

    def __repr__(self) -> str:
        return self.__str__()

    def __iter__(self):
        return trajectory_generator(self.video_path, self.json_path)


class ContiguousTrajectoryWindow:
    def __init__(self, trajectory: ContiguousTrajectory, frames_per_window: int=4):
        self.trajectory = trajectory
        self.frames_per_window = frames_per_window

    @property
    def task_id(self):
        return self.trajectory.task_id

    def __iter__(self):
        self._iter = 0
        self._trajectory_iterator = iter(self.trajectory)
        self.window = []
        return self

    def __next__(self, populating: bool = False):
        is_first = self._iter == 0
        self._iter += 1

        # should auto-raise StopIteration
        frame, action = self._trajectory_iterator.__next__()

        self.window.append((frame, action))
        if len(self.window) > self.frames_per_window:
            self.window.pop(0)

        if is_first:
            # let the window fully populate
            for _ in range(self.frames_per_window - 1):
                self.__next__(populating=True)

        if populating:
            return

        if len(self.window) != self.frames_per_window:
            raise ValueError(f"Unexpected window size. Got {len(self.window)}, expected: {self.frames_per_window}")

        return self.window


class ContiguousTrajectoryDataLoader:
    def __init__(self, dataset_path: str, task_id: int):
        self.dataset_path = dataset_path
        self.task_id = task_id

        # gather all unique IDs for every video/json file pair.
        unique_ids = glob(os.path.join(self.dataset_path, "*.mp4"))
        unique_ids = list(set([os.path.basename(x).split(".")[0] for x in unique_ids]))
        self.unique_ids = unique_ids

        # create ContiguousTrajectory objects for every mp4/json file pair.
        self.trajectories = []
        for unique_id in unique_ids:
            video_path = os.path.abspath(os.path.join(self.dataset_path, unique_id + ".mp4"))
            json_path = os.path.abspath(os.path.join(self.dataset_path, unique_id + ".jsonl"))
            t = ContiguousTrajectory(video_path, json_path, unique_id, task_id)
            self.trajectories.append(t)

    def __len__(self):
        return len(self.trajectories)

    def __iter__(self):
        # trajectories are always randomly shuffled. frame ordering remains in-tact.
        self.permutation = torch.randperm(len(self))
        self._iter = 0
        return self

    def __next__(self):
        if self._iter >= len(self):
            raise StopIteration

        t_index = self.permutation[self._iter].item()
        yield self.trajectories[t_index]
        self._iter += 1

    def sample(self) -> ContiguousTrajectory:
        return np.random.choice(self.trajectories)

    def __str__(self):
        return f"ContiguousTrajectoryDataLoader(n={len(self)}, {self.dataset_path})"

class DataHandler:
    def __init__(self, dataset_paths: List[str], frames_per_window: int):
        self.dataset_paths = dataset_paths
        self.frames_per_window = frames_per_window
        self.loaders = [ContiguousTrajectoryDataLoader(path, task_id) for task_id, path in enumerate(self.dataset_paths)]
    
    @property
    def num_tasks(self):
        # one loader per task
        return len(self.loaders)

    def sample_trajectories_for_each_task(self):
        # from each task dataset, sample 1 trajectory.
        # TODO: maybe sample more than 1 per task
        return [loader.sample() for loader in self.loaders]

    def sample_single_trajectory(self):
        task_id = np.random.randint(low=0, high=self.num_tasks)
        trajectory = self.loaders[task_id].sample()
        return ContiguousTrajectoryWindow(trajectory, frames_per_window=self.frames_per_window)


# class ExpertDatasetUnroller:
#     """Generates a window of state embeddings generated by a MineRLAgent. The agent
#     assumes that the trajectories are fed in contiguously. This class is meant to be iterated over!
#     """

#     def __init__(self, agent: MineRLAgent, window_size: int=4):
#         self.agent = agent
#         self.window_size = window_size

#     def __iter__(self):
#         self._iter = 0

#         # TODO: reset the agent's internal state and populate these with a FULL TRAJECTORY!
#         self.expert_observations = []
#         self.expert_actions = []
#         self._expert_pairs = zip(self.expert_observations, self.expert_actions)

#         self.window = []

#     def __next__(self, dont_yield: bool=False):
#         is_first = self._iter == 0
#         self._iter += 1

#         # should auto-raise StopIteration
#         expert_observation, expert_action = self._expert_pairs.__next__()

#         # precompute the expert embeddings
#         expert_embedding = self.agent.get_embedding(expert_observation)
#         self.window.append((expert_embedding, expert_action))
#         if len(self.window) > self.window_size:
#             self.window.pop(0)

#         if is_first:
#             # let the window fully populate
#             for _ in range(self.window_size - 1):
#                 self.__next__(dont_yield=True)

#         if len(self.window) != self.window_size:
#             raise ValueError(f"Unexpected window size. Got {len(self.window)}, expected: {self.window_size}")

#         if not dont_yield:
#             yield self.window

#     def decompose_window(self, window: List):
#         embeddings = []
#         actions = []
#         for embedding, action in window:
#             embeddings.append(embedding)
#             actions.append(action)
#         return embeddings, actions