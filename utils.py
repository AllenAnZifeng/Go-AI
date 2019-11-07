import argparse
import os
import sys

import torch
from mpi4py import MPI
from tqdm import tqdm

from go_ai import data, game
from go_ai.models import value_model, actorcritic_model
import time


def hyperparameters():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=bool, default=False, help='continue from checkpoint')

    parser.add_argument('--boardsize', type=int, help='board size')
    parser.add_argument('--mcts', type=int, default=0, help='monte carlo searches')

    parser.add_argument('--temp', type=float, default=1 / 64, help='initial temperature')
    parser.add_argument('--tempsteps', type=float, default=8, help='first k steps to apply temperature to pi')
    parser.add_argument('--lr', type=float, default=1e-3, help='learning rate')

    parser.add_argument('--batchsize', type=int, default=64, help='batch size')
    parser.add_argument('--replaysize', type=int, default=400000, help='max replay memory size')
    parser.add_argument('--trainsize', type=int, default=1000 * 64, help='train data size for one iteration')

    parser.add_argument('--iterations', type=int, default=128, help='iterations')
    parser.add_argument('--episodes', type=int, default=256, help='episodes')
    parser.add_argument('--evaluations', type=int, default=256, help='episodes')
    parser.add_argument('--eval-interval', type=int, default=1, help='iterations per evaluation')

    parser.add_argument('--episodesdir', type=str, default='episodes/', help='directory to store episodes')
    parser.add_argument('--checkpath', type=str, default='checkpoints/checkpoint.pt', help='model path for checkpoint')
    parser.add_argument('--tmppath', type=str, default='checkpoints/tmp.pt', help='model path for temp model')
    parser.add_argument('--trajpath', type=str, help='path for sample trajectory')
    
    parser.add_argument('--agent', type=str, choices=['mcts', 'ac'], default='mcts', help='type of agent/model')

    return parser.parse_args()

def parallel_play(comm, go_env, pi1, pi2, gettraj, episodes):
    """
    Plays games in parallel
    :param comm:
    :param go_env:
    :param pi1:
    :param pi2:
    :param gettraj:
    :param episodes:
    :return:
    """
    timestart = time.time()
    worker_episodes = episodes // comm.Get_size()
    winrate, traj = game.play_games(go_env, pi1, pi2, gettraj, worker_episodes, progress=False)
    winrate = comm.allreduce(winrate, op=MPI.SUM) / comm.Get_size()
    timeend = time.time()
    duration = timeend - timestart
    return winrate, traj, duration / worker_episodes

def sync_checkpoint(rank, comm: MPI.Intracomm, newcheckpoint_pi, checkpath, other_pi):
    if rank == 0:
        torch.save(newcheckpoint_pi.pytorch_model.state_dict(), checkpath)
    comm.Barrier()
    # Update other policy
    other_pi.pytorch_model.load_state_dict(torch.load(checkpath))


def parallel_out(rank, s):
    """
    Only the first worker prints stuff
    :param rank:
    :param s:
    :return:
    """
    if rank == 0:
        print(s, flush=True)


def parallel_err(rank, s):
    """
    Only the first worker prints stuff
    :param rank:
    :param s:
    :return:
    """
    if rank == 0:
        tqdm.write(f"{time.strftime('%H:%M:%S', time.localtime())}\t{s}", file=sys.stderr)


def sync_data(rank, comm: MPI.Intracomm, args):
    if rank == 0:
        if args.checkpoint:
            assert os.path.exists(args.checkpath)
        else:
            # Clear worker data
            episodesdir = args.episodesdir
            data.clear_episodesdir(episodesdir)
            # Set parameters
            if args.agent == 'mcts':
                new_model = value_model.ValueNet(args.boardsize)
            elif args.agent == 'ac':
                new_model = actorcritic_model.ActorCriticNet(args.boardsize)
            torch.save(new_model.state_dict(), args.checkpath)
    parallel_err(rank, "Using checkpoint: {}".format(args.checkpoint))
    comm.Barrier()
