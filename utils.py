import argparse
import os
import sys

import torch
from mpi4py import MPI
from tqdm import tqdm

from go_ai import data, game, policies
from go_ai.models import value, actorcritic
import time
import math
import datetime


def hyperparameters():
    today = str(datetime.date.today())

    parser = argparse.ArgumentParser()

    # Go Environment
    parser.add_argument('--boardsize', type=int, default=9, help='board size')
    parser.add_argument('--reward', type=str, choices=['real', 'heuristic'], default='real', help='reward system')

    # Monte Carlo Tree Search
    parser.add_argument('--mcts', type=int, default=0, help='monte carlo searches')
    parser.add_argument('--branches', type=int, default=4, help='branch degree for searching')
    parser.add_argument('--depth', type=int, default=3, help='search depth')

    # Learning Parameters
    parser.add_argument('--lr', type=float, default=1e-3, help='learning rate')

    # Exploration
    parser.add_argument('--temp', type=float, default=1 / 10, help='initial temperature')
    parser.add_argument('--tempsteps', type=float, default=8, help='first k steps to apply temperature to pi')

    # Data Sizes
    parser.add_argument('--batchsize', type=int, default=32, help='batch size')
    parser.add_argument('--replaysize', type=int, default=200000, help='max replay memory size')
    parser.add_argument('--trainsize', type=int, default=1000 * 32, help='train data size for one iteration')

    # Training
    parser.add_argument('--checkpoint', type=bool, default=False, help='continue from checkpoint')
    parser.add_argument('--iterations', type=int, default=128, help='iterations')
    parser.add_argument('--episodes', type=int, default=32, help='episodes')
    parser.add_argument('--evaluations', type=int, default=32, help='episodes')
    parser.add_argument('--eval-interval', type=int, default=1, help='iterations per evaluation')

    # Disk Data
    parser.add_argument('--episodesdir', type=str, default='bin/episodes/', help='directory to store episodes')
    parser.add_argument('--savedir', type=str, default=f'bin/baselines/{today}/')
    parser.add_argument('--basepath', type=str, default=f'bin/{today}/base.pt', help='model path for baseline model')

    # Model
    parser.add_argument('--agent', type=str, choices=['mcts', 'ac', 'mcts-ac'], default='mcts', help='type of agent/model')
    parser.add_argument('--baseagent', type=str, choices=['mcts', 'ac', 'mcts-ac', 'rand', 'greedy', 'human'],
        default='rand', help='type of agent/model for baseline')
    parser.add_argument('--resblocks', type=int, default=4, help='number of basic blocks for resnets')

    # Hardware
    parser.add_argument('--device', type=str, choices=['cpu', 'cuda'], default='cpu', help='device for pytorch models')

    return parser.parse_args()

def parallel_play(comm: MPI.Intracomm, go_env, pi1, pi2, gettraj, req_episodes):
    """
    Plays games in parallel
    :param comm:
    :param go_env:
    :param pi1:
    :param pi2:
    :param gettraj:
    :param req_episodes:
    :return:
    """
    rank = comm.Get_rank()
    world_size = comm.Get_size()

    worker_episodes = int(math.ceil(req_episodes / world_size))
    episodes = worker_episodes * world_size
    single_worker = comm.Get_size() <= 1

    timestart = time.time()
    winrate, steps, traj = game.play_games(go_env, pi1, pi2, gettraj, worker_episodes, progress=single_worker)
    timeend = time.time()

    duration = timeend - timestart
    avg_time = comm.allreduce(duration / worker_episodes, op=MPI.SUM) / world_size
    winrate = comm.allreduce(winrate, op=MPI.SUM) / world_size
    avg_steps = comm.allreduce(sum(steps), op=MPI.SUM) / episodes

    parallel_err(rank, f'{pi1} V {pi2} | {episodes} GAMES, {avg_time:.1f} SEC/GAME, {avg_steps:.0f} STEPS/GAME, '
                       f'{100 * winrate:.1f}% WIN')
    return winrate, traj

def sync_checkpoint(rank, comm: MPI.Intracomm, newcheckpoint_pi, checkpath, other_pi):
    if rank == 0:
        torch.save(newcheckpoint_pi.pytorch_model.state_dict(), checkpath)
    comm.Barrier()
    # Update other policy
    other_pi.pytorch_model.load_state_dict(torch.load(checkpath))


def parallel_out(rank, s, rep=0):
    """
    Only the first worker prints stuff
    :param rank:
    :param s:
    :return:
    """
    if rank == rep:
        print(s, flush=True)


def parallel_err(rank, s, rep=0):
    """
    Only the first worker prints stuff
    :param rank:
    :param s:
    :return:
    """
    if rank == rep:
        tqdm.write(f"{time.strftime('%H:%M:%S', time.localtime())}\t{s}", file=sys.stderr)
        sys.stderr.flush()


def sync_data(rank, comm: MPI.Intracomm, args):
    if rank == 0:
        checkpath = os.path.join(args.savedir, 'checkpoint.pt')
        if args.checkpoint:
            assert os.path.exists(checkpath)
        else:
            # Clear worker data
            episodesdir = args.episodesdir
            data.clear_episodesdir(episodesdir)
            # Save new model
            new_model, _ = create_agent(args, '', load_checkpoint=False)

            if not os.path.exists(args.savedir):
                os.mkdir(args.savedir)
            torch.save(new_model.state_dict(), checkpath)
    parallel_err(rank, "Using checkpoint: {}".format(args.checkpoint))
    comm.Barrier()


def create_agent(args, name, use_base=False, load_checkpoint=True):
    agent = args.baseagent if use_base else args.agent
    if agent == 'mcts':
        model = value.ValueNet(args.boardsize, args.resblocks)
        pi = policies.MCTS(name, model, args.mcts, args.temp, args.tempsteps)
    elif agent == 'ac':
        model = actorcritic.ActorCriticNet(args.boardsize)
        pi = policies.ActorCritic(name, model)
    elif agent == 'mcts-ac':
        model = actorcritic.ActorCriticNet(args.boardsize)
        pi = policies.MCTSActorCritic(name, model, args.branches, args.depth)
    elif agent == 'rand':
        model = None
        pi = policies.RAND_PI
    elif agent == 'greedy':
        model = None
        pi = policies.GREEDY_PI
    elif agent == 'human':
        model = None
        pi = policies.HUMAN_PI
    else:
        raise Exception("Unknown agent argument", agent)

    if load_checkpoint and not use_base:
        check_path = os.path.join(args.savedir, 'checkpoint.pt')
        model.load_state_dict(torch.load(check_path))

    return model, pi
