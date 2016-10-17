import gym
import argparse
import calendar

from rllab.baselines.linear_feature_baseline import LinearFeatureBaseline
from rllab.envs.box2d.box2d_env import Box2DEnv
from rllab.envs.box2d.cartpole_env import CartpoleEnv
from rllab.envs.normalized_env import normalize

from rllab.envs.gym_env import GymEnv

import rltools.util
from rltools.envs.julia_sim import JuliaEnvWrapper, FollowingWrapper, JuliaEnv
from rltools.envs.drive import DriveEnv_1D

from sandbox import RLLabRunner

from sandbox.rocky.tf.algos.trpo import TRPO
from sandbox.rocky.tf.algos.gail import GAIL
from sandbox.rocky.tf.envs.base import TfEnv

from sandbox.rocky.tf.policies.categorical_mlp_policy import CategoricalMLPPolicy
from sandbox.rocky.tf.policies.gaussian_mlp_policy import GaussianMLPPolicy

from sandbox.rocky.tf.policies.gaussian_lstm_policy import GaussianLSTMPolicy
from sandbox.rocky.tf.policies.gaussian_gru_policy import GaussianGRUPolicy

from sandbox.rocky.tf.core.network import MLP, RewardMLP, BaselineMLP
from sandbox.rocky.tf.optimizers.conjugate_gradient_optimizer import ConjugateGradientOptimizer, FiniteDifferenceHvp

import tensorflow as tf
import numpy as np
import os

parser = argparse.ArgumentParser()
# Logger Params
parser.add_argument('--exp_name',type=str,default='my_exp')
parser.add_argument('--tabular_log_file',type=str,default= 'tab.txt')
parser.add_argument('--text_log_file',type=str,default= 'tex.txt')
parser.add_argument('--params_log_file',type=str,default= 'args.txt')
parser.add_argument('--snapshot_mode',type=str,default='all')
parser.add_argument('--log_tabular_only',type=bool,default=False)
parser.add_argument('--log_dir',type=str)
parser.add_argument('--args_data')

# Environment params
parser.add_argument('--trajdatas',type=int,nargs='+',default=[1,2,3,4,5,6])
parser.add_argument('--n_features',type=int,default=45)
parser.add_argument('--limit_trajs',type=int,default=12000)
parser.add_argument('--max_traj_len',type=int,default=100)  # max length of a trajectory (ts)
parser.add_argument('--env_name',type=str,default="Following")
#parser.add_argument('--args_data',type=str)
parser.add_argument('--following_distance',type=int,default=20)
parser.add_argument('--normalize',type=bool,default= True)

parser.add_argument('--render',type=bool, default= False)

# Model Params
parser.add_argument('--policy_type',type=str,default='mlp')
parser.add_argument('--baseline_type',type=str,default='mlp')
parser.add_argument('--reward_type',type=str,default='mlp')
parser.add_argument('--load_policy',type=bool,default=False)

parser.add_argument('--hspec',type=int,nargs='+') # specifies architecture of "feature" networks
parser.add_argument('--p_hspec',type=int,nargs='+',default=[]) # policy layers
parser.add_argument('--b_hspec',type=int,nargs='+',default=[]) # baseline layers
parser.add_argument('--r_hspec',type=int,nargs='+',default=[]) # reward layers

parser.add_argument('--gru_dim',type=int,default=64) # hidden dimension of gru

parser.add_argument('--use_batchnorm',type=int,default=0)

## not implemented
#parser.add_argument('--match_weight',type=float,default=0.0) # how much to reward matching the expert hidden activations
#parser.add_argument('--match_ix',type=int,default=2) # which expert layer to match

# TRPO Params
parser.add_argument('--trpo_batch_size', type=int, default= 40 * 100)

parser.add_argument('--discount', type=float, default=0.95)
parser.add_argument('--gae_lambda', type=float, default=0.99)
parser.add_argument('--n_iter', type=int, default=500)  # trpo iterations

parser.add_argument('--max_kl', type=float, default=0.01)
parser.add_argument('--vf_max_kl', type=float, default=0.01)
parser.add_argument('--vf_cg_damping', type=float, default=0.01)

parser.add_argument('--trpo_step_size',type=float,default=0.1)

parser.add_argument('--only_trpo',type=bool,default=False)

# GAILS Params
parser.add_argument('--gail_batch_size', type=int, default= 1024)

# parser.add_argument('--decay', type=float, nargs=2, default=[0.96,10.])
parser.add_argument('--adam_steps',type=int,default=1)
parser.add_argument('--adam_lr', type=float, default=0.00005)
parser.add_argument('--adam_beta1',type=float,default=0.9)
parser.add_argument('--adam_beta2',type=float,default=0.99)
parser.add_argument('--adam_epsilon',type=float,default=1e-8)

parser.add_argument('--policy_ent_reg', type=float, default=0.0)
parser.add_argument('--env_r_weight',type=float,default=0.0)

args = parser.parse_args()

from rl_filepaths import expert_trajs_path as path

if args.hspec is None:
    p_hspec = args.p_hspec
    b_hspec = args.b_hspec
    r_hspec = args.r_hspec
else:
    p_hspec = args.hspec
    b_hspec = args.hspec
    r_hspec = args.hspec

if args.env_name == 'Following':

    env_id = "Following-v0"

    FollowingWrapper.set_initials(args.following_distance)

    gym.envs.register(
        id=env_id,
        entry_point='rltools.envs.julia_sim:FollowingWrapper',
        timestep_limit=999,
        reward_threshold=195.0,
    )

    expert_data_path = path + '/one_d/matchdist_n3000_t150_f3_d{}.h5'.format(args.following_distance)

    SWAP= True

elif args.env_name == "Auto2D":
    env_id = "Auto2D-v0"

    expert_data_path = path + '/features%i_mtl100_seed456_trajdata%s_openaiformat.h5'%(
        args.n_features,''.join([str(n) for n in args.trajdatas]))

    env_dict = {'trajdata_indeces': args.trajdatas}
    JuliaEnvWrapper.set_initials(args.env_name, 1, {})
    gym.envs.register(
        id=env_id,
        entry_point='rltools.envs.julia_sim:JuliaEnvWrapper',
        timestep_limit=999,
        reward_threshold=195.0,
    )

    SWAP= False

expert_data, expert_stats = rltools.util.load_trajs(expert_data_path,args.limit_trajs, swap = SWAP)
expert_data_stacked  = rltools.util.prepare_trajs(expert_data['exobs_B_T_Do'], expert_data['exa_B_T_Da'], expert_data['exlen_B'],
                                                  labeller= None)
expert_data = {'obs':expert_data_stacked['exobs_Bstacked_Do'],
               'act':expert_data_stacked['exa_Bstacked_Da']}

initial_obs_mean = expert_stats['obs_mean']
initial_obs_var = np.square(expert_stats['obs_std'])

g_env = normalize(GymEnv(env_id),
                  initial_obs_mean= initial_obs_mean,
                  initial_obs_var= initial_obs_var,
                  normalize_obs= True,
                  running_obs= False)

env = TfEnv(g_env) # this works

# create policy
if args.policy_type == 'mlp':
    policy = GaussianMLPPolicy('mlp_policy', env.spec, hidden_sizes= p_hspec,
                               std_hidden_nonlinearity=tf.nn.tanh,hidden_nonlinearity=tf.nn.tanh)

elif args.policy_type == 'gru':
    feat_mlp = MLP('mlp_policy', env.action_dim, p_hspec, tf.nn.tanh, tf.nn.tanh,
                   input_shape= (np.prod(env.spec.observation_space.shape),))
    policy = GaussianGRUPolicy(name= 'gru_policy', env_spec= env.spec,
                               hidden_dim= args.gru_dim,
                              feature_network=feat_mlp,
                              state_include_action=False)
else:
    raise NotImplementedError

# create baseline
if args.baseline_type == 'linear':
    baseline = LinearFeatureBaseline(env_spec=env.spec)

elif args.baseline_type == 'mlp':
    baseline = BaselineMLP(name='mlp_baseline',
                           output_dim=1,
                           hidden_sizes= b_hspec,
                           hidden_nonlinearity=tf.nn.tanh,
                           output_nonlinearity=None,
                           input_shape=(np.prod(env.spec.observation_space.shape),))
    baseline.initialize_optimizer()
else:
    raise NotImplementedError

# create adversary
reward = RewardMLP('mlp_reward', 1, r_hspec, tf.nn.tanh,tf.nn.sigmoid,
                       input_shape= (np.prod(env.spec.observation_space.shape) + env.action_dim,)
                       )

if not args.only_trpo:
    algo = GAIL(
        env=env,
        policy=policy,
        baseline=baseline,
        reward=reward,
        expert_data=expert_data,
        batch_size= args.trpo_batch_size,
        gail_batch_size=args.gail_batch_size,
        max_path_length=args.max_traj_len,
        n_itr=args.n_iter,
        discount=args.discount,
        #step_size=0.01,
        step_size=args.trpo_step_size,
        force_batch_sampler= True,
        whole_paths= True,
        adam_steps= args.adam_steps,
        fo_optimizer_cls= tf.train.AdamOptimizer,
        fo_optimizer_args= dict(learning_rate = args.adam_lr,
                                beta1 = args.adam_beta1,
                                beta2 = args.adam_beta2,
                                epsilon= args.adam_epsilon),
        optimizer=ConjugateGradientOptimizer(hvp_approach=FiniteDifferenceHvp(base_eps=1e-5))
    )
else:
    print("TRPO Only.")
    algo = TRPO(
        env=env,
        policy=policy,
        baseline=baseline,
        batch_size=args.trpo_batch_size,
        max_path_length=args.gail_batch_size,
        n_itr=args.n_iter,
        discount=args.discount,
        step_size=args.trpo_step_size,
        force_batch_sampler= True,
        whole_paths= True,
        optimizer=ConjugateGradientOptimizer(hvp_approach=FiniteDifferenceHvp(base_eps=1e-5)))

date= calendar.datetime.date.today().strftime('%y-%m-%d')
if date not in os.listdir('../data'):
    os.mkdir('../data/'+date)

c = 0
exp_name = args.exp_name + '-'+str(c)

while exp_name in os.listdir('../data/'+date+'/'):
    c += 1
    exp_name = args.exp_name + '-'+str(c)

runner = RLLabRunner(algo,args, date+'/'+exp_name)
runner.train()

halt= True