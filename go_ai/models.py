import numpy as np
import tensorflow as tf
from tensorflow.keras import layers
import gym
from tqdm import tqdm
from go_ai import mcts
from sklearn.preprocessing import normalize

from go_ai.mcts import GoGame

go_env = gym.make('gym_go:go-v0', size=0)
govars = go_env.govars


def make_actor_critic(board_size, critic_mode='val_net', critic_activation='tanh'):
    action_size = board_size ** 2 + 1

    inputs = layers.Input(shape=(board_size, board_size, 6), name="board")
    valid_inputs = layers.Input(shape=(action_size,), name="valid_moves")
    invalid_values = layers.Input(shape=(action_size,), name="invalid_values")

    x = inputs

    x = layers.Conv2D(64, kernel_size=3, padding='same')(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU()(x)
    x = layers.Conv2D(64, kernel_size=3, padding='same')(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU()(x)

    # Actor
    move_probs = layers.Conv2D(2, kernel_size=1)(x)
    move_probs = layers.BatchNormalization()(move_probs)
    move_probs = layers.ReLU()(move_probs)
    move_probs = layers.Flatten()(move_probs)
    move_probs = layers.Dense(action_size)(move_probs)
    move_probs = layers.Add()([move_probs, invalid_values])
    move_probs = layers.Softmax(name="move_probs")(move_probs)
    actor_out = move_probs

    # Critic
    move_vals = layers.Conv2D(1, kernel_size=1)(x)
    move_vals = layers.BatchNormalization()(move_vals)
    move_vals = layers.ReLU()(move_vals)
    move_vals = layers.Flatten()(move_vals)

    move_vals = layers.Dense(action_size)(move_vals)
    move_vals = layers.ReLU()(move_vals)
    if critic_mode == 'q_net':
        move_vals = layers.Dense(action_size, activation=critic_activation)(move_vals)
        move_vals = layers.Multiply(name="move_vals")([move_vals, valid_inputs])
    elif critic_mode == 'val_net':
        move_vals = layers.Dense(1, activation=critic_activation, name="value")(move_vals)
    else:
        raise Exception("Unknown critic mode")

    model = tf.keras.Model(inputs=[inputs, valid_inputs, invalid_values], outputs=[actor_out, move_vals],
                           name='actor_critic')
    return model


def get_invalid_moves(states):
    """
    Returns 1's where moves are invalid and 0's where moves are valid
    Assumes shape to be [BATCH SIZE, BOARD SIZE, BOARD SIZE, 6]
    """
    assert len(states.shape) == 4
    batch_size = states.shape[0]
    board_size = states.shape[1]
    invalid_moves = states[:, :, :, govars.INVD_CHNL].reshape((batch_size, -1))
    invalid_moves = np.insert(invalid_moves, board_size ** 2, 0, axis=1)
    return invalid_moves


def get_valid_moves(states):
    return 1 - get_invalid_moves(states)


def get_invalid_values(states):
    """
    Returns the action values of the states where invalid moves have -infinity value (minimum value of float32)
    and valid moves have 0 value
    """
    invalid_moves = get_invalid_moves(states)
    invalid_values = np.finfo(np.float32).min * invalid_moves
    return invalid_values


def forward_pass(states, network, training):
    """
    Since the neural nets take in more than one parameter,
    this functions serves as a wrapper to forward pass the data through the networks.

    Automatically moves the channel to the last dimension if necessary

    :param states:
    :param network:
    :param training: Boolean parameter for layers like BatchNorm
    :return: action probs and state vals
    """
    if states.shape[1] != states.shape[2]:
        states = states.transpose(0, 2, 3, 1)
    invalid_moves = get_invalid_moves(states)
    invalid_values = get_invalid_values(states)
    valid_moves = 1 - invalid_moves
    return network([states.astype(np.float32),
                    valid_moves.astype(np.float32),
                    invalid_values.astype(np.float32)], training=training)


def update_temporal_difference(actor_critic, batched_mem, optimizer, iteration, tb_metrics):
    """
    Optimizes the actor over the batched memory
    """
    binary_cross_entropy = tf.keras.losses.BinaryCrossentropy()
    mean_squared_error = tf.keras.losses.MeanSquaredError()
    gamma = 9 / 10

    pbar = tqdm(batched_mem, desc='Optimization', leave=True, position=0)
    for states, actions, next_states, rewards, terminals, wins, mcts_action_probs in pbar:
        wins = wins[:, np.newaxis]
        terminals = terminals[:, np.newaxis]
        assert terminals.shape == wins.shape
        _, next_state_vals = forward_pass(next_states, actor_critic, training=True)
        assert next_state_vals.shape == wins.shape

        targets = (wins * terminals) + (1 - terminals) * gamma * next_state_vals

        with tf.GradientTape() as tape:
            move_prob_distrs, state_vals = forward_pass(states, actor_critic, training=True)

            # Actor
            move_loss = binary_cross_entropy(mcts_action_probs, move_prob_distrs)

            # Critic
            assert targets.shape == wins.shape
            val_loss = mean_squared_error(targets, state_vals)

            overall_loss = val_loss + move_loss

        tb_metrics['move_loss'].update_state(move_loss)
        tb_metrics['val_loss'].update_state(val_loss)

        tb_metrics['overall_loss'].update_state(overall_loss)

        wins_01 = np.copy(wins)
        wins_01[wins_01 < 0] = 0
        tb_metrics['pred_win_acc'].update_state(wins_01, state_vals > 0)

        # compute and apply gradients
        gradients = tape.gradient(overall_loss, actor_critic.trainable_variables)
        optimizer.apply_gradients(zip(gradients, actor_critic.trainable_variables))

        # Metrics
        pbar.set_postfix_str('{:.1f}% {:.3f}L'.format(100 * tb_metrics['pred_win_acc'].result().numpy(),
                                                      tb_metrics['val_loss'].result().numpy()))


def update_win_prediction(actor_critic, batched_mem, optimizer, iteration, tb_metrics):
    """
    Optimizes the actor over the batched memory
    """
    binary_cross_entropy = tf.keras.losses.BinaryCrossentropy()
    mean_squared_error = tf.keras.losses.MeanSquaredError()

    def val_func(states):
        _, move_vals = forward_pass(states, actor_critic, training=False)
        return move_vals.numpy().flatten()

    pbar = tqdm(batched_mem, desc='Updating', leave=True, position=0)
    for states, actions, next_states, rewards, terminals, wins in pbar:
        wins = wins[:, np.newaxis]

        qvals = get_qvals(states.transpose(0, 3, 1, 2), val_func)
        assert np.min(qvals) >= -1
        assert np.max(qvals) <= 1

        target_pi = ((qvals + 1)/2) + 1e-7
        target_pi = normalize(target_pi**8, norm='l1')
        with tf.GradientTape() as tape:
            move_prob_distrs, state_vals = forward_pass(states, actor_critic, training=True)

            # Actor
            move_loss = binary_cross_entropy(target_pi, move_prob_distrs)

            # Critic
            assert state_vals.shape == wins.shape
            val_loss = mean_squared_error(wins, state_vals)

            overall_loss = val_loss + move_loss

        tb_metrics['move_loss'].update_state(move_loss)
        tb_metrics['val_loss'].update_state(val_loss)

        tb_metrics['overall_loss'].update_state(overall_loss)

        wins_01 = (np.copy(wins) + 1) / 2
        tb_metrics['pred_win_acc'].update_state(wins_01, state_vals > 0)

        # compute and apply gradients
        gradients = tape.gradient(overall_loss, actor_critic.trainable_variables)
        optimizer.apply_gradients(zip(gradients, actor_critic.trainable_variables))

        # Metrics
        pbar.set_postfix_str('{:.1f}% {:.3f}L'.format(100 * tb_metrics['pred_win_acc'].result().numpy(),
                                                      tb_metrics['val_loss'].result().numpy()))


def get_qvals(states, val_func):
    """
    :param states:
    :param val_func:
    :return: Q values for each state (batch_size x action_size)
    """
    canonical_next_states = []
    for state in states:
        valid_moves = GoGame.get_valid_moves(state)
        valid_move_idcs = np.argwhere(valid_moves > 0).flatten()
        for move in valid_move_idcs:
            next_state = GoGame.get_next_state(state, move)
            next_turn = GoGame.get_turn(next_state)
            canonical_next_state = GoGame.get_canonical_form(next_state, next_turn)
            canonical_next_states.append(canonical_next_state)

    canonical_next_states = np.array(canonical_next_states)

    canonical_next_vals = val_func(canonical_next_states)
    curr_idx = 0
    qvals = []
    for state in states:
        valid_moves = GoGame.get_valid_moves(state)
        Qs = []
        for move in range(GoGame.get_action_size(state)):
            if valid_moves[move]:
                Qs.append(-canonical_next_vals[curr_idx])
                curr_idx += 1
            else:
                Qs.append(0)

        qvals.append(Qs)

    assert curr_idx == len(canonical_next_vals), (curr_idx, len(canonical_next_vals))
    return np.array(qvals)