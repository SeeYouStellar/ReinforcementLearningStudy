# -*- coding: utf-8 -*-


import gym
import math
import random
import numpy as np
import matplotlib.pyplot as plt
from collections import namedtuple, deque
from itertools import count

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as T
from torchvision.transforms import InterpolationMode

np.random.seed(2)

env = gym.make("SpaceInvaders-v0")

# if gpu is to be used
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


######################################################################
# Replay Memory

Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward'))


class ReplayMemory(object):

    def __init__(self, capacity):
        self.memory = deque([], maxlen=capacity)

    def push(self, *args):
        self.memory.append(Transition(*args))

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.memory)


######################################################################
# DQN algorithm

class DQN(nn.Module):

    def __init__(self, h, w, outputs):
        super(DQN, self).__init__()
        self.conv1 = nn.Conv2d(4, 32, kernel_size=8, stride=4)
        self.bn1 = nn.BatchNorm2d(32)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=4, stride=2)
        self.bn2 = nn.BatchNorm2d(64)
        self.conv3 = nn.Conv2d(64, 64, kernel_size=3, stride=1)
        self.bn3 = nn.BatchNorm2d(64)

        # conv2d输出的神经元个数计算
        def conv2d_size_out(size, kernel_size, stride):
            return (size - (kernel_size - 1) - 1) // stride  + 1
        convw = conv2d_size_out(conv2d_size_out(conv2d_size_out(w, 8, 4), 4, 2), 3, 1)
        convh = conv2d_size_out(conv2d_size_out(conv2d_size_out(h, 8, 4), 4, 2), 3, 1)
        linear_input_size = convw * convh * 64
        self.l1 = nn.Linear(linear_input_size, 512)
        self.l2 = nn.Linear(512, outputs)

    def forward(self, x):
        x = x.to(device)    # (32, 4, 84, 84)
        x = self.bn1(self.conv1(x))  # (32, 32, 20, 20)
        x = self.bn2(self.conv2(x))  # (32, 64, 9, 9)
        x = self.bn3(self.conv3(x))  # (32, 64, 7, 7)
        x = F.relu(self.l1(x.view(x.size(0), -1)))  # (32, 512)
        return self.l2(x.view(-1, 512))  # (32, 6)


######################################################################
# Input extraction

resize = T.Compose([T.ToPILImage(),
                    T.Grayscale(num_output_channels=1),
                    T.Resize((84, 84), interpolation=InterpolationMode.BICUBIC),
                    T.ToTensor()])


def get_screen():
    # Transpose it into torch order (CHW).
    screen = env.render(mode='rgb_array').transpose((2, 0, 1))
    screen = np.ascontiguousarray(screen, dtype=np.float32) / 255
    screen = torch.from_numpy(screen)
    # Resize, and add a batch dimension (BCHW)
    return resize(screen).unsqueeze(0)  # (1, 4, 84, 84)


######################################################################
# Training

# 参数和网络初始化
BATCH_SIZE = 32
GAMMA = 0.99
# explore-exploite 这里感觉可以改进
EPS_START = 1.0
EPS_END = 0.1
EPS_DECAY = 100000
TARGET_UPDATE = 2400
# weight_file_path = 'weights/policy_net_weights_509.pth'

init_screen = get_screen()
_, _, screen_height, screen_width = init_screen.shape

# Get number of actions from gym action space
n_actions = env.action_space.n

# loaded_state_dict = torch.load(weight_file_path)

policy_net = DQN(screen_height, screen_width, n_actions).to(device)
# policy_net.load_state_dict(loaded_state_dict)
# print("load parameter from :"+weight_file_path)
target_net = DQN(screen_height, screen_width, n_actions).to(device)
target_net.load_state_dict(policy_net.state_dict())
target_net.eval()

optimizer = optim.RMSprop(policy_net.parameters())
memory = ReplayMemory(100000)

steps_done = 0


def select_action(state):
    global steps_done
    sample = random.random()
    eps_threshold = EPS_END + (EPS_START - EPS_END) * \
        math.exp(-1. * steps_done / EPS_DECAY)
    steps_done += 1
    if sample > eps_threshold:
        # 跑一下模型得出St所有动作里最大的的Q值的动作，但是不进行反向传播，故需要no_grad()
        with torch.no_grad():
            return policy_net(state).max(1)[1].view(1, 1) # net output:[1, 6] -> max(1)[1] -> 返回第1维（6）的最大值的索引 (1, 1) -> view(1, 1)改变张量维度 -> (1, 1)
    else: # 随机选择动作
        return torch.tensor([[random.randrange(n_actions)]], device=device, dtype=torch.long)  # (1, 1)


episode_durations = []


def plot_durations():
    plt.figure(1)
    plt.clf()
    durations_t = torch.tensor(episode_durations, dtype=torch.float)
    plt.title('Training...')
    plt.xlabel('Episode')
    plt.ylabel('Duration')
    plt.plot(durations_t.numpy())
    # Take 100 episode averages and plot them too
    if len(durations_t) >= 100:
        means = durations_t.unfold(0, 100, 1).mean(1).view(-1) # 滑动切割每100个duration
        means = torch.cat((torch.zeros(99), means))
        plt.plot(means.numpy())

    plt.pause(0.001)  # pause a bit so that plots are updated


def optimize_model():
    if len(memory) < BATCH_SIZE:
        return
    transitions = memory.sample(BATCH_SIZE)
    # transition:(St, At, Rt, St+1)
    # transitions: batch_size个transition
    # batch: 打包transitions中的St, At, Rt, St+1，形成St元组，At元组，Rt元组， St+1元组
    # St元组：元组为batch_size个(4, 84, 84)的张量
    # At元组：元组为batch_size个(1)的张量

    batch = Transition(*zip(*transitions))

    # Compute a mask of non-final states and concatenate the batch elements
    # (a final state would've been the one after which simulation ended)
    non_final_mask = torch.tensor(tuple(map(lambda s: s is not None, batch.next_state)),
                                  device=device, dtype=torch.bool)
    non_final_next_states = torch.cat([s for s in batch.next_state if s is not None])

    state_batch = torch.cat(batch.state)  # 按第0维将张量合并 (batch_size, 4, 84, 84)
    # print(state_batch.shape)
    action_batch = torch.cat(batch.action)  # (batch_size, 1)
    # print(action_batch.shape)
    reward_batch = torch.cat(batch.reward)  # (batch_size, 1)
    # print(reward_batch.shape)

    state_action_values = policy_net(state_batch).gather(1, action_batch)
    next_state_values = torch.zeros(BATCH_SIZE, device=device)
    next_state_values[non_final_mask] = target_net(non_final_next_states).max(1)[0].detach()
    expected_state_action_values = (next_state_values * GAMMA) + reward_batch

    # Compute Huber loss
    criterion = nn.MSELoss()
    loss = criterion(state_action_values, expected_state_action_values.unsqueeze(1))

    # Optimize the model
    optimizer.zero_grad()
    loss.backward()
    for param in policy_net.parameters():
        param.grad.data.clamp_(-1, 1)
    optimizer.step()


def random_start(skip_steps=30, m=4):
    env.reset()
    state_queue = deque([], maxlen=m)
    stack_state = deque([], maxlen=m)
    for _ in range(skip_steps):
        for _ in range(m):
            stack_state.append(get_screen())
            action = env.action_space.sample()
            _, _, done, _ = env.step(action)
            if done:
                break
        state_queue.append(torch.maximum(stack_state[2], stack_state[3]))

    return done, state_queue, stack_state


######################################################################
# Start Training

num_episodes = 10000
m = 4
for i_episode in range(num_episodes):
    print('episode:', i_episode)
    print('steps_done:', steps_done)
    eps_t = EPS_END + (EPS_START - EPS_END) * math.exp(-1. * steps_done / EPS_DECAY)
    print('eps_threshold:', eps_t)
    # Initialize the environment and state
    done, state_queue, stack_state = random_start()
    if done:
        continue

    state = torch.cat(tuple(state_queue), dim=1)
    next_state_queue = state_queue.copy()
    for t in count():
        reward = 0
        m_reward = 0  # 四个连续帧的reward
        action = select_action(state)

        for _ in range(m):
            _, reward, done, _ = env.step(action.item())
            stack_state.append(get_screen())
            m_reward += reward
            if done:
                break
        next_state_queue.append(torch.maximum(stack_state[2], stack_state[3]))

        if not done:
            next_state = torch.cat(tuple(next_state_queue), dim=1)
        else:
            next_state = None
            m_reward = -150
        m_reward = torch.tensor([m_reward], device=device)

        memory.push(state, action, next_state, m_reward)

        state = next_state
        optimize_model()
        if done:
            episode_durations.append(t + 1)
            plot_durations()
            break

        # Update the target network, copying all weights and biases in DQN
        if steps_done % TARGET_UPDATE == 0:
            target_net.load_state_dict(policy_net.state_dict())
            torch.save(policy_net.state_dict(), 'weights/policy_net_weights_{0}.pth'.format(i_episode))
            print('save torch: policy_net_weights_{0}.pth'.format(i_episode))

print('Complete')
env.close()
torch.save(policy_net.state_dict(), 'weights/policy_net_weights.pth')