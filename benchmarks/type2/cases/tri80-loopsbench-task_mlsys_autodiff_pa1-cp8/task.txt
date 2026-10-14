Finish the PA1 autodiff framework in `/workspace/pa1/`. The repository
already contains the computational-graph scaffolding; the missing work is the
actual node builders, forward operators, reverse-mode gradients, fused ops,
and the transformer/training glue that sits on top of them.

The work falls into three connected areas:

`pa1/auto_diff.py`
Restore the graph-building API itself: `Variable`, input nodes, the global op
instances such as `add`, `mul`, `sum_op`, `broadcast`, `matmul`, `softmax`,
`layernorm`, `relu`, `sqrt`, `power`, and `mean`, plus the `Node`
operator-overload dispatch (`__add__`, `__sub__`, `__rsub__`, `__mul__`,
`__truediv__`). Then implement the forward `compute` and backward `gradient`
methods for the primitive arithmetic, comparison, reduction, broadcast,
linear-algebra, normalization, activation, and utility ops. Finish the graph
utilities `topological_sort`, `Evaluator.run`, and `gradients`.

`pa1/fused_ops.py`
Implement the fused matmul-plus-layernorm and matmul-plus-softmax builders,
along with their forward and backward logic.

`pa1/transformer.py`
Build the single-head attention encoder graph in `transformer()`, implement
`softmax_loss()` and the scaffolded `sgd_epoch()` loop, and finish
`train_model()` so it creates the graph, binds runtime tensors, and
initializes model weights in the expected order
`[W_Q, W_K, W_V, W_O, W_1, W_2, b_1, b_2]`.

Keep the public graph API exactly as the skeleton defines it. The tests call
the graph-construction helpers directly, including `Variable`, `add`,
`sum_op`, `broadcast`, `matmul`, `softmax`, `layernorm`,
`matmul_layernorm`, and `matmul_softmax`, and they probe methods such as
`Evaluator.run` and `gradients` individually.

All gradient implementations must return `List[Node]` and build the backward
graph with the existing ops; they must not return eager `torch.Tensor`
values.
