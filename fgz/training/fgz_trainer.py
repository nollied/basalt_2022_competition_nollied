import minerl
import gym

from fractal_zero.search.fmc import FMC
from fractal_zero.data.tree_sampler import TreeSampler

from vpt.agent import MineRLAgent

from fgz.data_utils.data_handler import ExpertDatasetUnroller

class FGZTrainer:

    def __init__(
        self,
        minerl_env: gym.Env,
        agent: MineRLAgent,
        fmc: FMC,
        unroll_steps: int=8,
    ):
        self.minerl_env = minerl_env
        self.agent = agent

        # TODO: create dynamics function externally as the vectorized environment inside FMC.
        # self.dynamics_function = DynamicsFunction(
        #     state_embedding_size=state_embedding_size,
        #     discriminator_classes=2,
        # )
        self.fmc = fmc
        self.unroll_steps = unroll_steps

    def get_fmc_trajectory(self, root_embedding, max_steps: int=None):
        self.fmc.reset()
        self.fmc.vectorized_environment.set_all_states(None, root_embedding)

        # make sure the trajectory that comes out doesn't exceed the comparing trajectory
        steps = self.unroll_steps if max_steps else min(max_steps, self.unroll_steps)
        self.fmc.simulate(steps)

        self.tree_sampler = TreeSampler(self.fmc.tree, sample_type="best_path")
        observations, actions, _ = self.tree_sampler.get_batch()
        return observations, actions

    def train_trajectory(self):
        # 2 trajectories are generated. 1 is from the expert dataset, where the entire trajectory is loaded
        # as a contiguous piece. 

        # sample a FULL trajectory from the expert dataset.

        # the trajectory is fed in 1 observation at a time to the agent. the agent then predicts an action.
        # this action prediction can more or less be discarded (or it doesn't need to be calculated at all)
        # what we really care about is gathering the embedding that goes into the policy head and setting that as
        # the dynamics function's state.

        unroller = ExpertDatasetUnroller(self.agent, window_size=self.unroll_steps + 1)
        for expert_sequence in unroller:
            start_embedding, _ = expert_sequence[0]  # "representation function"

            fmc_dynamics_embeddings, fmc_actions = self.get_fmc_trajectory(
                start_embedding, 
                max_steps=self.unroll_steps
            )
            expert_embeddings, expert_actions = unroller.decompose_window(expert_sequence[1:])