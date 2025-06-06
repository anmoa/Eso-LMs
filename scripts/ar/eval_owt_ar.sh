#!/bin/bash
#SBATCH -J eval_ar                # Job name
#SBATCH -o watch_folder/%x_%j.out     # log file (out & err)
#SBATCH -N 1                          # Total number of nodes requested
#SBATCH --get-user-env                # retrieve the users login environment
#SBATCH --mem=100000                  # server memory requested (per node)
#SBATCH -t 960:00:00                  # Time limit (hh:mm:ss)
#SBATCH --partition=thickstun          # Request partition
#SBATCH --constraint="[a5000|a6000|a100|3090]"
#SBATCH --constraint="gpu-high"
#SBATCH --ntasks-per-node=4
#SBATCH --gres=gpu:4                  # Type/number of GPUs needed
#SBATCH --open-mode=append            # Do not overwrite logs
#SBATCH --requeue                     # Requeue upon preemption

# checkpoint_path=/share/kuleshov/ssahoo/textdiffusion/text-diffusion-exp-v4-AgBZrc-small-ar-param-ar_data-openwebtext-split_seqlen-1024_maxs-1300001_bs-512/checkpoints/last.ckpt
checkpoint_path=/share/kuleshov/ds2456/neurips2025/new_ar.ckpt

export HYDRA_FULL_ERROR=1

srun python -u -m main \
  mode=ppl_eval \
  loader.batch_size=32 \
  loader.eval_batch_size=32 \
  data=openwebtext-split \
  +data.insert_train_special=False \
  +data.insert_valid_special=False \
  algo=ar \
  model.length=1024 \
  eval.checkpoint_path=$checkpoint_path \
  +wandb.offline=true
