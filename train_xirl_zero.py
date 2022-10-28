

from xirl_zero.main_trainer import Config, Trainer


if __name__ == "__main__":
    # TODO: determine based on task idx
    # dataset_path = "/Volumes/CORSAIR/data/MineRLBasaltMakeWaterfall-v0"
    dataset_path = "./data/MineRLBasaltMakeWaterfall-v0"

    config = Config(dataset_path=dataset_path)
    trainer = Trainer(config)
    trainer.train_step()