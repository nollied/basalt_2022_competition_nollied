
import ray
from vpt.agent import MineRLAgent
from fgz.architecture.dynamics_function import DynamicsFunction

from fgz.data_utils.xirl_data import XIRLDataHandler
from vpt.run_agent import load_agent


# @ray.remote
class XIRLTrainer:

    def __init__(self, dataset_path: str, model_path: str, weights_path: str):
        # NOTE: we can't use the same agent without more complicated thread-safeness code.
        self.agent = load_agent(model_path, weights_path)

        self.dynamics_function = DynamicsFunction(embedder_layers=2)

        self.data_handler = XIRLDataHandler(dataset_path, self.agent, self.dynamics_function)

    def train_on_pair(self):
        self.t0, self.t1 = self.data_handler.sample_pair()
        print("bytes for the pair of trajectories:", self.get_nbytes_stored())

        # TODO: cycle consistency loss + train

    def get_nbytes_stored(self):
        nbytes0 = sum([e.nelement() * e.element_size() for e in self.t0])
        nbytes1 = sum([e.nelement() * e.element_size() for e in self.t1])
        return nbytes0 + nbytes1

