import io
import numpy as np
import tensorflow as tf
from matplotlib import pyplot as plt
from tqdm import tqdm
import gym

from go_ai import data, policies, mcts
from go_ai.models import actor_critic, value_model

go_env = gym.make('gym_go:go-v0', size=0)
gogame = go_env.gogame

def matplot_format(state):
    """
    :param state:
    :return: A formatted state for matplot imshow.
    Only uses the piece and pass channels of the original state
    """
    assert len(state.shape) == 3
    return state.transpose(1, 2, 0)[:, :, [0, 1, 4]].astype(np.float)


def plot_move_distr(title, move_distr, valid_moves, scalar=None):
    """
    Takes in a 1d array of move values and plots its heatmap
    """
    board_size = int((len(move_distr) - 1) ** 0.5)
    plt.axis('off')
    valid_values = np.extract(valid_moves[:-1] == 1, move_distr[:-1])
    assert np.isnan(move_distr).any() == False, move_distr
    pass_val = float(move_distr[-1])
    plt.title(title + (' ' if scalar is None else ' {:.3f}S').format(scalar)
              + '\n{:.3f}L {:.3f}H {:.3f}P'.format(np.min(valid_values)
                                                   if len(valid_values) > 0 else 0,
                                                   np.max(valid_values)
                                                   if len(valid_values) > 0 else 0,
                                                   pass_val))
    plt.imshow(np.reshape(move_distr[:-1], (board_size, board_size)))


def state_responses_helper(policy_args, states, taken_actions, next_states, rewards, terminals, wins):
    """
    Helper function for state_responses
    :param policy_args:
    :param states:
    :param taken_actions:
    :param next_states:
    :param rewards:
    :param terminals:
    :param wins:
    :return:
    """

    def action_1d_to_2d(action_1d, board_width):
        """
        Converts 1D action to 2D or None if it's a pass
        """
        if action_1d == board_width ** 2:
            action = None
        else:
            action = (action_1d // board_width, action_1d % board_width)
        return action

    board_size = states[0].shape[1]

    if policy_args['mode'] == 'actor_critic':
        model = actor_critic.make_actor_critic(board_size)
        model.load_weights(policy_args['model_path'])
        forward_func = actor_critic.make_forward_func(model)
        move_probs, move_vals = forward_func(states)

        state_vals = tf.reduce_sum(move_probs * move_vals, axis=1)
        _, qvals, _ = mcts.pi_qval_from_actor_critic(states, forward_func=forward_func)
    elif policy_args['mode'] == 'values':
        model = value_model.make_val_net(board_size)
        model.load_weights(policy_args['model_path'])
        forward_func = value_model.make_forward_func(model)
        policy = policies.GreedyPolicy(forward_func)
        move_probs = []
        for i, state in enumerate(states):
            move_probs.append(policy(state, i))
        state_vals = forward_func(states)
        qvals = mcts.qval_from_stateval(states, forward_func)

    else:
        raise Exception("Unknown policy mode")

    valid_moves = data.get_valid_moves(states)

    num_states = states.shape[0]
    num_cols = 4

    fig = plt.figure(figsize=(num_cols * 2.5, num_states * 2))
    for i in range(num_states):
        curr_col = 1

        plt.subplot(num_states, num_cols, curr_col + num_cols * i)
        plt.axis('off')
        plt.title('Board')
        plt.imshow(matplot_format(states[i]))
        curr_col += 1

        plt.subplot(num_states, num_cols, curr_col + num_cols * i)
        plot_move_distr('Q Vals', qvals[i], valid_moves[i], scalar=state_vals[i].item())
        curr_col += 1

        plt.subplot(num_states, num_cols, curr_col + num_cols * i)
        plot_move_distr('Model', move_probs[i], valid_moves[i])
        curr_col += 1

        plt.subplot(num_states, num_cols, curr_col + num_cols * i)
        plt.axis('off')
        plt.title('Taken Action: {}\n{:.0f}R {}T, {}W'
                  .format(action_1d_to_2d(taken_actions[i], board_size), rewards[i], terminals[i], wins[i]))
        plt.imshow(matplot_format(next_states[i]))
        curr_col += 1

    plt.tight_layout()
    return fig


def state_responses(policy_args, replay_mem):
    """
    :param policy_args: The model
    :param replay_mem: List of events
    :return: The figure visualizing responses of the model
    on those events
    """
    states, actions, next_states, rewards, terminals, wins = data.replay_mem_to_numpy(replay_mem)
    assert len(states[0].shape) == 3 and states[0].shape[1] == states[0].shape[2], states[0].shape

    fig = state_responses_helper(policy_args, states, actions, next_states, rewards, terminals, wins)
    return fig


def gen_traj_fig(go_env, policy_args):
    board_size = policy_args['board_size']
    weight_path = policy_args['model_path']

    if policy_args['mode'] == 'actor_critic':
        model = actor_critic.make_actor_critic(board_size)
        model.load_weights(weight_path)
        policy = policies.ActorCriticPolicy(actor_critic)
    elif policy_args['mode'] == 'values':
        model = value_model.make_val_net(board_size)
        model.load_weights(weight_path)
        forward_func = value_model.make_forward_func(model)
        policy = policies.GreedyPolicy(forward_func)
    else:
        raise Exception("Unknown policy mode")

    black_won, traj = data.self_play(go_env, policy=policy, get_trajectory=True)
    replay_mem = []
    data.add_traj_to_replay_mem(replay_mem, black_won, traj)
    fig = state_responses(policy_args, replay_mem)
    return fig


def plot_symmetries(next_state, outpath):
    symmetrical_next_states = gogame.get_symmetries(next_state)

    cols = len(symmetrical_next_states)
    plt.figure(figsize=(3 * cols, 3))
    for i, state in enumerate(symmetrical_next_states):
        plt.subplot(1, cols, i + 1)
        plt.imshow(matplot_format(state))
        plt.axis('off')

    plt.savefig(outpath)
    plt.close()


def figure_to_image(figure):
    """Converts the matplotlib plot specified by 'figure' to a PNG image and
    returns it. The supplied figure is closed and inaccessible after this call."""
    # Save the plot to a PNG in memory.
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    # Closing the figure prevents it from being displayed directly inside
    # the notebook.
    plt.close(figure)
    buf.seek(0)
    # Convert PNG buffer to TF image
    image = tf.image.decode_png(buf.getvalue(), channels=4)
    # Add the batch dimension
    image = tf.expand_dims(image, 0)
    return image


def reset_metrics(metrics):
    for key, metric in metrics.items():
        metric.reset_states()


def evaluate(go_env, my_policy, opponent_policy, num_games):
    win_metric = tf.keras.metrics.Mean()

    pbar = tqdm(range(num_games), desc='Evaluation', leave=True, position=0)
    for episode in pbar:
        if episode % 2 == 0:
            black_won, _ = data.pit(go_env, my_policy, opponent_policy)
            win = (black_won + 1) / 2

        else:
            black_won, _ = data.pit(go_env, opponent_policy, my_policy)
            win = (-black_won + 1) / 2

        win_metric.update_state(win)
        pbar.set_postfix_str('{} {:.1f}%'.format(win, 100 * win_metric.result().numpy()))

    return win_metric.result().numpy()
