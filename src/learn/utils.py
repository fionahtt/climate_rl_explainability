# @Theodore Wolf, @fionahtt

import numpy as np
import random
from IPython.display import clear_output
import torch
import shap
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from collections import defaultdict
from scipy import stats

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class ReplayBuffer:
    """To store experience for uncorrelated learning"""

    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = []
        self.position = 0

    def push(self, state, action, reward, next_state, done):
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.position] = (state, action, reward, next_state, done)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        state, action, reward, next_state, done = map(np.stack, zip(*batch))
        return state, action, reward, next_state, done
    
    # @fionahtt
    # modified sample function
    # returns the state and indices used to sample from buffer
    # for critical states experiment in explainability function

    def sample_with_indices(self, batch_size):
        indices = random.sample(range(len(self.buffer)), batch_size)
        batch = [self.buffer[i] for i in indices]
        state, action, reward, next_state, done = map(np.stack, zip(*batch))
        return state, indices

    def __len__(self):  
        return len(self.buffer)


def plot(data_dict):
    """For tracking experiment progress"""
    rewards = data_dict['moving_avg_rewards']
    std = data_dict['moving_std_rewards']
    frame_idx = data_dict['frame_idx']
    clear_output(True)
    plt.figure(figsize=(20, 5))
    plt.subplot(131)
    plt.title('frame %s. reward: %s' % (frame_idx, rewards[-1]))
    plt.plot(rewards)
    reward = np.array(rewards)
    stds = np.array(std)
    plt.fill_between(np.arange(len(reward)), reward - 0.25 * stds, reward + 0.25 * stds, color='b', alpha=0.1)
    plt.fill_between(np.arange(len(reward)), reward - 0.5 * stds, reward + 0.5 * stds, color='b', alpha=0.1)
    plt.show()


def plot_test_trajectory(env, agent, fig, axes, max_steps=600, test_state=None, fname=None,):
    """To plot trajectories of the agent"""
    state = env.reset_for_state(test_state)
    learning_progress = []
    for step in range(max_steps):
        list_state = env.get_plot_state_list()

        # take recommended action
        action = agent.get_action(state, testing=True)

        # Do the new chosen action in Environment
        new_state, reward, done, _ = env.step(action)

        learning_progress.append([list_state, action, reward])

        state = new_state
        if done:
            break

    fig, axes = env.plot_run(learning_progress, fig=fig, axes=axes, fname=fname, )

    return fig, axes


class PER_IS_ReplayBuffer:
    """
    Adapted from https://github.com/labmlai/annotated_deep_learning_paper_implementations
    """

    def __init__(self, capacity, alpha, state_dim=3):
        self.capacity = capacity
        self.alpha = alpha
        self.priority_sum = [0 for _ in range(2 * self.capacity)]
        self.priority_min = [float('inf') for _ in range(2 * self.capacity)]
        self.max_priority = 1.
        self.data = {
            'obs': np.zeros(shape=(capacity, state_dim), dtype=np.float64),
            'action': np.zeros(shape=capacity, dtype=np.int32),
            'reward': np.zeros(shape=capacity, dtype=np.float32),
            'next_obs': np.zeros(shape=(capacity, state_dim), dtype=np.float64),
            'done': np.zeros(shape=capacity, dtype=np.bool)
        }
        self.next_idx = 0
        self.size = 0

    def push(self, obs, action, reward, next_obs, done):
        idx = self.next_idx
        self.data['obs'][idx] = obs
        self.data['action'][idx] = action
        self.data['reward'][idx] = reward
        self.data['next_obs'][idx] = next_obs
        self.data['done'][idx] = done

        self.next_idx = (idx + 1) % self.capacity
        self.size = min(self.capacity, self.size + 1)

        priority_alpha = self.max_priority ** self.alpha
        self._set_priority_min(idx, priority_alpha)
        self._set_priority_sum(idx, priority_alpha)

    def _set_priority_min(self, idx, priority_alpha):
        idx += self.capacity
        self.priority_min[idx] = priority_alpha
        while idx >= 2:
            idx //= 2
            self.priority_min[idx] = min(self.priority_min[2 * idx], self.priority_min[2 * idx + 1])

    def _set_priority_sum(self, idx, priority_alpha):
        idx += self.capacity
        self.priority_sum[idx] = priority_alpha
        while idx >= 2:
            idx //= 2
            self.priority_sum[idx] = self.priority_sum[2 * idx] + self.priority_sum[2 * idx + 1]

    def _sum(self):
        return self.priority_sum[1]

    def _min(self):
        return self.priority_min[1]

    def find_prefix_sum_idx(self, prefix_sum):
        idx = 1
        while idx < self.capacity:
            if self.priority_sum[idx * 2] > prefix_sum:
                idx = 2 * idx
            else:
                prefix_sum -= self.priority_sum[idx * 2]
                idx = 2 * idx + 1

        return idx - self.capacity

    def sample(self, batch_size, beta):

        samples = {
            'weights': np.zeros(shape=batch_size, dtype=np.float32),
            'indexes': np.zeros(shape=batch_size, dtype=np.int32),
        }

        for i in range(batch_size):
            p = random.random() * self._sum()
            idx = self.find_prefix_sum_idx(p)
            samples['indexes'][i] = idx

        prob_min = self._min() / self._sum()
        max_weight = (prob_min * self.size) ** (-beta)

        for i in range(batch_size):
            idx = samples['indexes'][i]
            prob = self.priority_sum[idx + self.capacity] / self._sum()
            weight = (prob * self.size) ** (-beta)
            samples['weights'][i] = weight / max_weight

        for k, v in self.data.items():
            samples[k] = v[samples['indexes']]

        return samples

    def update_priorities(self, indexes, priorities):

        for idx, priority in zip(indexes, priorities):
            self.max_priority = max(self.max_priority, priority)
            priority_alpha = priority ** self.alpha
            self._set_priority_min(idx, priority_alpha)
            self._set_priority_sum(idx, priority_alpha)

    def is_full(self):
        return self.capacity == self.size

    def __len__(self):
        return self.size


def feature_importance(agent_net, buffer, n_points, v=False, scalar=False):
    features = ["A", "Y", "S"]
    if v:
        features = ["A", "Y", "S", "dA", "dY", "dS"]

    data = buffer.sample(n_points)[0]

    explainer = shap.DeepExplainer(agent_net,
                                   torch.from_numpy(data).float().to(DEVICE))
    shap_q_values = explainer.shap_values(torch.from_numpy(data).float().to(DEVICE))
    if scalar:
        shap_values = np.array(shap_q_values)
    else:
        shap_values = np.array(np.sum(shap_q_values, axis=0))
    shap.summary_plot(shap_values,
                      features=data,
                      feature_names=features,
                      plot_type='violin', show=False, sort=False)

# @fionahtt
# currently specifically for DQN agents
def explainability_plots(agent_net, buffer, n_points, q_values, actions, 
                         v=False, bar=True, summary=True, dependence=True):
    actions_names = ["default", "DG", "ET", "DG+ET"]
    
    data, indices = buffer.sample_with_indices(n_points)

    SHAP_plots(agent_net, data, actions_names, v, bar, summary, dependence)
    
    #Q-values and actions corresponding with sampled states
    sampled_q_values = [q_values[i] for i in indices]
    sampled_actions = [actions[i] for i in indices]
    
    #names of actions selected
    sampled_actions_names = [actions_names[i] for i in sampled_actions]

    # Q-value difference calculations
    # for each sample, difference between max Q-value and average of Q-values
    max_q_values = [sampled_q_values[i][sampled_actions[i]] 
                    for i in range(len(sampled_actions))]
    avg_q_values = np.array(np.sum(sampled_q_values, axis=1))/4
    q_differences = max_q_values - avg_q_values

    plot_Q_differences(q_differences, sampled_actions_names)

    p_values = critical_states_tests(q_differences, sampled_actions_names)
    print("One-sample ANOVA test p-value: " + str(p_values["ANOVA"]))
    print("One-sample t-test p-value: " + str(p_values["t-test"]))

def SHAP_plots(agent_net, data, actions, v=False, 
               bar=True, summary=True, dependence=True):
    features = ["A", "Y", "S"]
    if v:
        features = ["A", "Y", "S", "dA", "dY", "dS"]

    explainer = shap.DeepExplainer(agent_net,
                                   torch.from_numpy(data).float().to(DEVICE))
    shap_q_values = explainer.shap_values(torch.from_numpy(data).float().to(DEVICE))

    """
    # test of Q-values
    print(shap_q_values[0][0])
    print(shap_q_values[1][0])
    print(shap_q_values[2][0])
    print(shap_q_values[3][0])
    """

    #working with Q-values SHAP instead of state values SHAP
    shap_values = shap_q_values
    #average of SHAP values of 4 Q-values
    avg_shap_values = np.array(np.sum(shap_q_values, axis=0))/4

    if bar:
        plot_bar(shap_values, data, features, actions)

    if summary:
        #use all Q-values vs use one Q-value vs use avg of Q-values?
        plot_summary(avg_shap_values, data, features)
        #plot_summary(shap_values[0], data, features)
        #plot_summary(shap_values[1], data, features)
        #plot_summary(shap_values[2], data, features)
        #plot_summary(shap_values[3], data, features)
        #plot_summary(avg_shap_values, data, features)

    if dependence:
        plot_dependence("A", avg_shap_values, data, features)
        plot_dependence("Y", avg_shap_values, data, features)
        plot_dependence("S", avg_shap_values, data, features)

    """
    if force:
        base_value = explainer.expected_value[0]
        plot_force(base_value, shap_values[0][0], data[0], features)
    """

def plot_summary(shap_values, data, features):
    shap.summary_plot(shap_values,
                      features=data,
                      feature_names=features,
                      plot_type='violin', sort=False)

def plot_bar(shap_values, data, features, actions):
    shap.summary_plot(shap_values,
                      features=data,
                      feature_names=features,
                      class_names = actions,
                      plot_type='bar', sort=False, 
                      plot_size = (8, 5))
    
def plot_dependence(feature, shap_values, data, features):
    shap.dependence_plot(feature,
                         shap_values = shap_values,
                         features = data,
                         feature_names = features,
                         interaction_index = None)
    
def plot_force(base_value, shap_values, data, features):
    shap.force_plot(base_value,
                    shap_values=shap_values,
                    features=data,
                    feature_names=features)

def plot_Q_differences(q_differences, sampled_actions_names):
    colours = {'default': 'red', 'DG': 'green', 
               'ET': 'blue', 'DG+ET': 'purple'}
    x_values = np.arange(len(q_differences))

    plt.figure(figsize=(20, 10))
    plt.bar(x_values, q_differences, 
            color=[colours[name] for name in sampled_actions_names])
    
    legend_labels = list(colours.keys())
    legend_handles = [plt.Rectangle((0, 0), 1, 1, color=colours[label]) 
                      for label in legend_labels]
    plt.legend(legend_handles, legend_labels, title="Actions")

    plt.xlabel("Sampled States")
    plt.ylabel("Q-Difference")
    plt.title("Q-Differences for Sampled States")
    plt.show()

def critical_states_tests(q_differences, sampled_actions_names):
    p_values = {}

    #make dictionary of q-difference values grouped by action taken
    q_diffs_groups = defaultdict(list)
    for q, action_name in zip(q_differences, sampled_actions_names):
        q_diffs_groups[action_name].append(q)
    q_diffs_by_action = {action: values for action, 
                         values in q_diffs_groups.items()}
    
    # for t-test
    # compare mean of action group with highest q-diff with overall mean
    # action group w highest q-diff has most critical states
    # (typically true, true in all these critical state plots)
    q_diff_mean = q_differences.mean()
    max_action = sampled_actions_names[np.argmax(q_differences)]
    q_diffs_max_action = q_diffs_by_action[max_action]

    #one-sample ANOVA test
    f_statistic, p_value_ANOVA = stats.f_oneway(*q_diffs_by_action.values())
    p_values["ANOVA"] = p_value_ANOVA

    #one-sample t-test
    t_statistic, p_value_t = stats.ttest_1samp(q_diffs_max_action, q_diff_mean, 
                                               alternative = 'greater')
    p_values["t-test"] = p_value_t

    return p_values

def plot_end_state_matrix(results):
    t = 1 # alpha value
    size = int(np.sqrt(len(results)))
    results[results==0.] = 4
    cmap = {1: [0., 0., 0., t], 2: [0., 1.0, 0., t], 3: [1.0, 0.1, 0.1, t], 4: [1., 1., 0., t]}
    labels = {1: r'$Black_{FP}$', 2: r'$Green_{FP}$', 3: r'$A_{PB}$', 4: r'$Y_{SF}$'}
    arrayShow = np.array([[cmap[i] for i in j] for j in results.reshape(size, size)])
    patches = [mpatches.Patch(color=cmap[i], label=labels[i]) for i in cmap]
    plt.imshow(arrayShow, extent=(0.45, 0.55, 0.55, 0.45))
    plt.legend(handles=patches, loc='upper left', bbox_to_anchor=(1, 1.))
    plt.ylabel("A")
    plt.xlabel("Y")


def plot_action_matrix(results):
    t = 1 # alpha value
    size = int(np.sqrt(len(results)))
    cmap = {0: [1.0, 0.1, 0.1, t], 1: [1., 0.5, 0., t], 2: [0.1, 1., 0.1, t], 3: [0., 0., 1., t]}
    labels = {0: 'Default', 1: 'DG', 2: 'ET', 3: 'DG+ET'}
    arrayShow = np.array([[cmap[i] for i in j] for j in results.reshape(size, size)])
    patches = [mpatches.Patch(color=cmap[i], label=labels[i]) for i in cmap]
    plt.imshow(arrayShow, extent=(0.45, 0.55, 0.55, 0.45))
    plt.legend(handles=patches, loc='upper left', bbox_to_anchor=(1, 1.))
    plt.ylabel("A")
    plt.xlabel("Y")
