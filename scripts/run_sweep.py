import wandb
import numpy as np


learning_rates = list(
    np.around(
        np.array(
            [np.linspace(0, 1, 10, endpoint=False)[1:] / 10 ** i for i in range(1, 6)]
        ).flatten(),
        decimals=6,
    ).tolist()
)
ent_rate = list(
    np.around(
        np.array(
            [np.linspace(0, 1, 10, endpoint=False)[1:] / 10 ** i for i in range(0, 4)]
        ).flatten(),
        decimals=6,
    ).tolist()
)


sweep_config = {
    "method": "bayes",
    "metric": {"name": "val_reward", "goal": "maximize"},
    "early_terminate": {"type": "hyperband", "min_iter": 40},
    "parameters": {
        "lr_model": {"distribution": "categorical", "values": learning_rates},
        "lr_decay": {
            "distribution": "categorical",
            "values": [0.99, 0.98, 0.97, 0.96, 0.95, 1.0],
        },
        "exp_beta": {
            "distribution": "categorical",
            "values": [1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65],
        },
        "ent_rate": {"distribution": "categorical", "values": ent_rate},
    },
}

sweep_id = wandb.sweep(sweep_config, project="CORL")
