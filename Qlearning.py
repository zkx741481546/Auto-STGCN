import argparse
import json
import mxnet as mx
import wandb
import dill
from ExperimentDataLogger import *
from Env import *
import numpy as np
from utils.utils import generate_action_dict
from collections import defaultdict
import os

# os.environ['MXNET_GPU_MEM_POOL_RESERVE'] = '50'
# os.environ['MXNET_CUDNN_AUTOTUNE_DEFAULT'] = '0'


class QTable:
    def __init__(self, config):
        self.training_stage_last = config['training_stage_last']
        assert not self.training_stage_last
        self.n = config['n']
        # key: tuple, state value: dict:key:tuple, with all possible actions, values:Q_values
        self.Qtable = defaultdict(lambda: defaultdict(lambda: 0.0))
        self.actions = generate_action_dict(self.n, self.training_stage_last)

    def get_Q_value(self, state, action):
        if isinstance(state, np.ndarray):
            state = tuple(state.tolist())
        if isinstance(action, np.ndarray):
            action = tuple(action.tolist())
        return self.Qtable[state][action]

    def get_action(self, state):
        # return (action, max_Q_value)
        if isinstance(state, np.ndarray):
            state = tuple(state.tolist())
        Q_values = []
        actions = self.actions[state[0]]
        for action in actions:
            action = tuple(action.tolist())
            Q_values.append(self.get_Q_value(state, action))
        Q_values = np.array(Q_values)
        Q_value = np.max(Q_values)
        action = np.array(list(actions[np.argmax(Q_values)]))
        return action, Q_value

    def set_Q_value(self, state, action, value):
        if isinstance(state, np.ndarray):
            state = tuple(state.tolist())
        if isinstance(action, np.ndarray):
            action = tuple(action.tolist())
        self.Qtable[state][action] = value


def train_QTable(config, config_name):
    #####################
    # set up parameters  #
    ######################
    opt = config['opt']
    lr = config['DQN_lr']
    episodes = config['episodes']
    exploration = config['exploration']
    gamma = config['gamma']
    n = config['n']
    training_stage_last = config['training_stage_last']
    exploration_decay_step = config["exploration_decay_step"]
    exploration_decay_rate = config["exploration_decay_rate"]
    if isinstance(config['ctx'], list):
        ctx = [mx.gpu(i) for i in config['ctx']]
    elif isinstance(config['ctx'], int):
        ctx = mx.gpu(config['ctx'])
    else:
        raise Exception("config_ctx error:" + str(config['ctx']))
    logger = Logger(config_name, config)

    #######################
    # init QTable and Env #
    #######################
    q_table = QTable(config)
    env = GNNEnv(config, ctx, logger)

    ##############
    #  training  #
    ##############
    # exploration_list = [(1000, 1.0), (1500, 0.9), (2000, 0.8), (2200, 0.7), (2500, 0.6), (2800, 0.5), (3000, 0.4),
    #                     (4000, 0.3)]
    episode = 0
    exception_cnt = False
    while episode < episodes or exception_cnt >= episodes:
        logger.set_episode(episode)
        start_time = time()
        print("====================================================")
        print(f"episode:{episode + 1}/{episodes}")
        # S{-2}
        obs = env.reset()
        done = False
        exception_flag = False
        # store trajectory and edit the reward
        local_buffer = []
        while not done:
            if np.random.random() >= exploration:
                action, _ = q_table.get_action(obs)
                print(f"state:\n{obs}\naction:{action}    QTable")
            else:
                action = generate_random_action(obs, n, training_stage_last)
                print(f"state:\n{obs}\naction:{action}    random")
            # s{-1}-S{T}, T<=n
            # => len(local_buffer)<= T+2
            logger(state=obs, action=action)
            next_obs, reward, done, info = env.step(action)
            exception_flag = info['exception_flag']
            local_buffer.append([obs, action, reward, next_obs, done])
            obs = next_obs
        # edit reward and add into buffer
        reward = local_buffer[-1][2] / len(local_buffer)
        if not exception_flag:
            wandb.log({"episode": episode, "reward": reward, "epsilon": exploration}, sync=False)
        print(f"    reward:{reward}")
        for i in range(len(local_buffer)):
            local_buffer[i][2] = reward
            logger(reward=reward)
        episode += 1
        # training
        for obs, action, reward, next_obs, done in local_buffer:
            if not done:
                q_S_A = q_table.get_Q_value(obs, action)
                q_table.set_Q_value(obs, action,
                                    q_S_A + lr * (reward + gamma * q_table.get_action(next_obs)[1] - q_S_A))
        # epsilon decay
        exploration *= pow(exploration_decay_rate, episode / exploration_decay_step)
        # for threshold, expl in exploration_list:
        #     if episode < threshold:
        #         exploration = expl
        if episode % 100 == 0:
            with open(logger.log_path + 'QTable.dill', 'wb') as f:
                dill.dump(q_table, f)
        episode_time = time() - start_time
        print(f"    episode_time_cost:{episode_time}")
        logger(time=episode_time)
        logger.update_data_units()
        logger.flush_log()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default=None)
    args = parser.parse_args()
    config_filename = args.config
    with open(config_filename, 'r') as f:
        config = json.loads(f.read())
    print(json.dumps(config, sort_keys=True, indent=4))
    wandb.init(project="GNN", config=config)
    train_QTable(config, config_filename.replace('./Config/', '').replace("/", "_").split('.')[0])
