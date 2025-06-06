import math
import os
import typing

import torch
import torch.nn.functional as F
import torchmetrics
import transformers

LOG2 = math.log(2)


class NLL(torchmetrics.aggregation.MeanMetric):
  def update(self,
             value:typing.Union[float, torch.Tensor],
             weight:typing.Union[float, torch.Tensor]=1.0) -> None:
    """Update state with data.

    Args:
      value: Either a float or tensor containing data.
        Additional tensor dimensions will be flattened
      weight: Either a float or tensor containing weights
        for calculating the average. Shape of weight should
        be able to broadcast with the shape of `value`.
        Default to `1.0` corresponding to simple harmonic
        average.
    """
    # broadcast weight to value shape
    if not isinstance(value, torch.Tensor):
        value = torch.as_tensor(value, dtype=self.dtype,
                                device=self.device)
    if (weight is not None
        and not isinstance(weight, torch.Tensor)):
      weight = torch.as_tensor(weight,
                               dtype=self.dtype,
                               device=self.device)
    weight = torch.broadcast_to(weight, value.shape)
    value, weight = self._cast_and_nan_check_input(value,
                                                   weight)

    if value.numel() == 0:
      return
    self.mean_value += value.sum()
    self.weight += weight.sum()


class BPD(NLL):
  def compute(self) -> torch.Tensor:
    """Computes the bits per dimension.

    Returns:
      bpd
    """
    return self.mean_value / self.weight / LOG2


class Perplexity(NLL):
  def compute(self) -> torch.Tensor:
    """Computes the Perplexity.

    Returns:
     Perplexity
    """
    return torch.exp(self.mean_value / self.weight)


class Metrics:
  def __init__(self, gen_ppl_eval_model_name_or_path=None,
               eval_ppl_batch_size=None) -> None:
    metrics = torchmetrics.MetricCollection({
        'nll': NLL(), 'bpd': BPD(), 'ppl': Perplexity()})
    metrics.set_dtype(torch.float64)
    self.train_nlls = metrics.clone(prefix='train/')
    self.train_recons = BPD()
    self.valid_nlls = metrics.clone(prefix='val/')
    self.valid_recons = BPD()
    self.gen_ppl = Perplexity()
    self.sample_entropy = torchmetrics.aggregation.MeanMetric()
    self.eval_ppl_batch_size = eval_ppl_batch_size
    self.gen_ppl_eval_model_name_or_path = gen_ppl_eval_model_name_or_path
    self.tokenizer = transformers.AutoTokenizer.\
      from_pretrained(gen_ppl_eval_model_name_or_path)
    if self.tokenizer.pad_token is None:
      self.tokenizer.pad_token = self.tokenizer.eos_token
      self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

  def to(self, *args, **kwargs):
    self.gen_ppl = self.gen_ppl.to(*args, **kwargs)
    self.sample_entropy = self.sample_entropy.to(*args, **kwargs)
    self.train_nlls = self.train_nlls.to(*args, **kwargs)
    self.train_recons = self.train_recons.to(*args, **kwargs)
    self.valid_nlls = self.valid_nlls.to(*args, **kwargs)
    self.valid_recons = self.valid_recons.to(*args, **kwargs)

  def reset(self):
    self.gen_ppl.reset()
    self.sample_entropy.reset()
    self.train_nlls.reset()
    self.train_recons.reset()
    self.valid_nlls.reset()
    self.valid_recons.reset()

  def update_train(self, nll, recons_loss, num_tokens):
    self.train_nlls.update(nll, num_tokens)
    self.train_recons.update(recons_loss, num_tokens)

  def update_valid(self, nll, recons_loss, num_tokens):
    self.valid_nlls.update(nll, num_tokens)
    self.valid_recons.update(recons_loss, num_tokens)


  @torch.no_grad()
  def _eval_retokenize(self, text_samples, max_length,
                       device):
    """Retokenizes samples for the eval model.
    
    Args:
        text_samples: List of sentences generated by the model.
    Returns:
        samples: Samples re-tokenized for the eval model
        attn_mask: Attention mask for the eval model
        eval_context_size: Size of the context for the eval model
    """
    if 'llama2' in self.gen_ppl_eval_model_name_or_path:
      tokenizer_kwargs = {
        'text_samples': text_samples,
        'return_tensors': 'pt',
        'return_token_type_ids': False,
        'return_attention_mask': True,
        'truncation': True,
        'padding': True,
        'max_length': max_length,
      }
      eval_context_size = 4096
    else:
      tokenizer_kwargs = {
        'return_tensors': 'pt',
        'return_token_type_ids': False,
        'return_attention_mask': True,
        'truncation': True,
        'padding': True,
        'max_length': max_length,
      }
      eval_context_size = 1024
    samples = self.tokenizer(text_samples,
                             **tokenizer_kwargs)
    attn_mask = samples['attention_mask']
    samples = samples['input_ids']
    if 'llama2' not in self.gen_ppl_eval_model_name_or_path:
      attn_mask = attn_mask.to(device)
      samples = samples.to(device)      
    return samples, attn_mask, eval_context_size

  @torch.no_grad()
  def record_entropy(self, tokens):
    for sample in tokens:
      _, counts = torch.unique(
        sample, return_counts=True, sorted=False)
      entropy = torch.special.entr(
        counts.float() / counts.sum()).sum().item()
      self.sample_entropy.update(entropy)

  @torch.no_grad()
  def record_generative_perplexity(
    self,
    text_samples: typing.List[str],
    max_length: int,
    retokenize: bool = True,
    device='cuda') -> None:
    
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    eval_model = transformers.AutoModelForCausalLM.from_pretrained(
      self.gen_ppl_eval_model_name_or_path).eval()
    if 'llama2' not in self.gen_ppl_eval_model_name_or_path:
      eval_model = eval_model.to(device)
    # Re-tokenize using eval model's tokenizer
    if retokenize:
      (samples, attn_mask,
       eval_context_size) = self._eval_retokenize(
         text_samples, max_length=max_length, device=device)
    else:
      samples = text_samples
      attn_mask = torch.ones(samples.shape).to(device)
      eval_context_size = samples.shape[-1]
    batch_size = min(self.eval_ppl_batch_size,
                     samples.shape[0])
    num_batches = samples.shape[0] // batch_size
    for i in range(num_batches):
      _samples = torch.split(
        samples[i * batch_size: (i + 1) * batch_size],
        eval_context_size,
        dim=-1)
      _attn_mask = torch.split(
        attn_mask[i * batch_size: (i + 1) * batch_size],
        eval_context_size,
        dim=-1)
      for (sample_chunk, attn_mask_chunk) in zip(_samples,
                                                 _attn_mask):
        logits = eval_model(sample_chunk,
                            attention_mask=attn_mask_chunk)
        logits = logits[0].transpose(-1, -2)
        nlls = F.cross_entropy(logits[..., :-1],
                               sample_chunk[..., 1:],
                               reduction='none')
        first_eos = (
          sample_chunk
          == self.tokenizer.eos_token_id).cumsum(-1) == 1
        token_mask = sample_chunk != self.tokenizer.eos_token_id
        valid_tokens = first_eos[..., 1:] + token_mask[..., 1:]
        self.gen_ppl.update(nlls * valid_tokens, valid_tokens)
