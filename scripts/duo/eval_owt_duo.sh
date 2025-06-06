#!/bin/bash
#SBATCH -J owt_duo_anneal                    # Job name
#SBATCH -o watch_folder/%x_%j.out     # log file (out & err)
#SBATCH -N 1                          # Total number of nodes requested
#SBATCH --get-user-env                # retrieve the users login environment
#SBATCH --mem=100000                  # server memory requested (per node)
#SBATCH -t 960:00:00                  # Time limit (hh:mm:ss)
#SBATCH --partition=kuleshov          # Request partition
#SBATCH --constraint="[a5000|a6000|a100|3090]"
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:1                  # Type/number of GPUs needed
#SBATCH --open-mode=append            # Do not overwrite logs
#SBATCH --requeue                     # Requeue upon preemption

checkpoint_path=/share/kuleshov/ssahoo/flow-ode/flow-ode-VlCQLK-small-conjugate-OWT-anneal/checkpoints/last.ckpt

export HYDRA_FULL_ERROR=1

srun python -u -m main \
  mode=ppl_eval \
  loader.batch_size=8 \
  loader.eval_batch_size=8 \
  data=openwebtext-split \
  model=small \
  algo=duo_base \
  eval.checkpoint_path=$checkpoint_path \
  sampling.num_sample_batches=0 \
  +wandb.offline=true
