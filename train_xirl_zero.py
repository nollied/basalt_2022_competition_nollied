

from warnings import warn
import wandb
from xirl_zero.main_trainer import Config, Trainer

import torch



# TODO: determine based on task idx
# DATASET_PATH = "/Volumes/CORSAIR/data/MineRLBasaltMakeWaterfall-v0"
DATASET_PATH = "./data/MineRLBasaltMakeWaterfall-v0"

OUTPUT_DIR = "./train/xirl_zero/"

SMOKE_TEST = True
USE_WANDB = False

SMOKE_CONFIG = Config(
    dataset_path=DATASET_PATH,
    train_steps=5,
    eval_every=1,
    eval_steps=5,
    checkpoint_every=1,
    max_frames=10,
    max_trajectories=10,
    use_wandb=USE_WANDB,
    model_log_frequency=1,
)
CONFIG = Config(
    dataset_path=DATASET_PATH,
    train_steps=10_000,
    eval_every=100,
    eval_steps=0,
    checkpoint_every=100,
    max_frames=None,
    max_trajectories=None,
    use_wandb=USE_WANDB,
    model_log_frequency=1000,
)

if __name__ == "__main__":
    config = SMOKE_CONFIG if SMOKE_TEST else CONFIG

    if SMOKE_TEST:
        warn("\n\n\n\n\n\nWARNING: DOING A SMOKE TEST!\n\n\n\n\n\n")
    print("Using config:", config.asdict())

    if config.use_wandb:
        wandb.init(project="xirl_zero", config=config.asdict())

    trainer = Trainer(config)

    def run_eval(config: Config):
        for _ in range(config.eval_steps):
            trainer.eval_step()

    def run_train(config: Config):
        for step in range(config.train_steps):
            trainer.train_step()

            is_last_step = step == (config.train_steps - 1)

            if (step + 1) % config.eval_every == 0 or is_last_step:
                run_eval(config)

            if (step + 1) % config.checkpoint_every == 0 or is_last_step:
                trainer.checkpoint(OUTPUT_DIR)
                _, target_state = trainer.generate_and_save_target_state(OUTPUT_DIR)

    run_train(config)
