import unittest

import gym
import torch

import go_ai.policies.actorcritic
import go_ai.policies.baselines
import go_ai.policies.value
from go_ai import game
from go_ai.models import value, actorcritic



class MyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        board_size = 5

        class Args:
            width = 26
            depth = 0
            gamma = 0
            temp = 0

        self.greedy_mct_policy = go_ai.policies.value.Value('MCTGreedy', go_ai.policies.baselines.greedy_val_func,
                                                            Args)

        self.go_env = gym.make('gym_go:go-v0', size=board_size)

        self.num_games = 256

    def test_val_vs_val(self):
        size = 5
        self.go_env = gym.make('gym_go:go-v0', size=size)
        new_model = value.ValueNet(size)
        new_model.load_state_dict(torch.load(f'../../bin/checkpoints/2020-01-20/val{size}.pt'))

        val_model = value.ValueNet(size)
        val_model.load_state_dict(torch.load(f'../../bin/baselines/val{size}.pt'))

        class Args:
            width = 26
            depth = 0
            gamma = 0
            temp = 0.05

        new_pi = go_ai.policies.value.Value('New', new_model, Args)
        base_pi = go_ai.policies.value.Value('Base', val_model, Args)

        win_rate, _, _, _ = game.play_games(self.go_env, new_pi, base_pi, self.num_games)
        print(win_rate)
        self.assertGreaterEqual(win_rate, 0.6)

    def test_mctval_vs_val(self):
        """
        Custom test case to test trained models
        """
        size = 5
        self.go_env = gym.make('gym_go:go-v0', size=size)
        curr_model = value.ValueNet(size)
        curr_model.load_state_dict(torch.load(f'../../bin/baselines/val{size}.pt'))

        class MCTArgs:
            width = 4
            depth = 5
            gamma = 0.9
            temp = 0.03

        class BaseArgs:
            width = 0
            depth = 0
            gamma = 0
            temp = 0

        mct_pi = go_ai.policies.value.Value('MCT', curr_model, MCTArgs)
        val_pi = go_ai.policies.value.Value('MCT', curr_model, BaseArgs)

        win_rate, _, _, _ = game.play_games(self.go_env, mct_pi, val_pi, self.num_games)
        print(win_rate)
        self.assertGreaterEqual(win_rate, 0.6)

    def test_val_vs_ac(self):
        self.go_env = gym.make('gym_go:go-v0', size=9)
        ac_model = actorcritic.ActorCriticNet(9)
        ac_model.load_state_dict(torch.load('../../bin/baselines/ac9.pt'))

        val_model = value.ValueNet(9)
        val_model.load_state_dict(torch.load('../../bin/baselines/val9.pt'))

        mct_pi = go_ai.policies.actorcritic.ActorCritic('AC', ac_model, mcts=81, temp=1)
        val_pi = go_ai.policies.value.Value('Val', val_model, width=8, temp=0.05)

        win_rate, _, _, _ = game.play_games(self.go_env, val_pi, mct_pi, self.num_games)
        print(win_rate)
        self.assertGreaterEqual(win_rate, 0.6)

    def test_mctac_vs_ac(self):
        """
        Custom test case to test trained models
        """
        self.go_env = gym.make('gym_go:go-v0', size=9)
        curr_model = actorcritic.ActorCriticNet(9)
        curr_model.load_state_dict(torch.load('../../bin/baselines/ac.pt'))

        mct_pi = go_ai.policies.actorcritic.ActorCritic('MCT', curr_model, mcts=4, temp=1)
        val_pi = go_ai.policies.actorcritic.ActorCritic('MCT', curr_model, mcts=0, temp=1)

        win_rate, _, _, _ = game.play_games(self.go_env, mct_pi, val_pi, self.num_games)
        print(win_rate)
        self.assertGreaterEqual(win_rate, 0.6)

    def test_mct_vs_greed(self):
        win_rate, _, _, _ = game.play_games(self.go_env, self.greedy_mct_policy, go_ai.policies.baselines.GREEDY_PI,
                                            self.num_games)
        print(win_rate)
        self.assertGreaterEqual(win_rate, 0.6)

    def test_greed_vs_rand(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.GREEDY_PI,
                                            go_ai.policies.baselines.RAND_PI, self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 1, delta=0.1)

    def test_smartgreed_vs_greed(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.SMART_GREEDY_PI,
                                            go_ai.policies.baselines.GREEDY_PI, False,
                                            self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 0.5, delta=0.1)

    def test_greed_vs_greed(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.GREEDY_PI,
                                            go_ai.policies.baselines.GREEDY_PI, self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 0.5, delta=0.1)

    def test_rand_vs_greed(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.RAND_PI,
                                            go_ai.policies.baselines.GREEDY_PI, self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 0, delta=0.1)

    def test_greedy_and_basegreedymct_are_equal(self):
        self.greedy_mct_policy.width = 0
        done = False
        state = self.go_env.get_canonical_state()
        while not done:
            greedy_pi = go_ai.policies.baselines.GREEDY_PI(state, None)
            mct_pi = self.greedy_mct_policy(state, None)
            self.assertTrue((greedy_pi == mct_pi).all(), (state[:2], greedy_pi, mct_pi))
            a = self.go_env.uniform_random_action()
            _, _, done, _ = self.go_env.step(a)
            self.greedy_mct_policy.step(a)
            state = self.go_env.get_canonical_state()

    def test_mct_vs_rand(self):
        win_rate, _, _, _ = game.play_games(self.go_env, self.greedy_mct_policy, go_ai.policies.baselines.RAND_PI,
                                            self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 1, delta=0.1)

    def test_rand_vs_mct(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.RAND_PI, self.greedy_mct_policy,
                                            self.num_games)
        print(win_rate)
        self.assertAlmostEqual(win_rate, 1, delta=0.1)

    def test_greed_vs_mct(self):
        win_rate, _, _, _ = game.play_games(self.go_env, go_ai.policies.baselines.GREEDY_PI, self.greedy_mct_policy,
                                            self.num_games)
        print(win_rate)
        self.assertLessEqual(win_rate, 0.2)


if __name__ == '__main__':
    unittest.main()
