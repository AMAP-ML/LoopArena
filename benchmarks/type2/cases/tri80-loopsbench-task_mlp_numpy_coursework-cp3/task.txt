This is the Machine Learning Practical (MLP) coursework framework used in an introductory deep-learning course. It provides a NumPy-based neural network library (`mlp` package) for building and training neural networks from scratch. Multiple core modules have been left unimplemented and must be completed so that all tests pass.

Your working directory is `/workspace`. The project code is already present at `/workspace/mlp/`.

Implement the following modules:

The following modules need to be implemented or substantially built out:

layers (`mlp/layers.py`): Neural network layer implementations including affine (fully connected) transformations, activation functions (sigmoid, ReLU, leaky ReLU, ELU, SELU, tanh, softmax), radial basis function projections, dropout, reshape, batch normalization, and 2D convolution. In addition to the forward/backward passes, this task now also hollows constructor and parameter-interface logic for the affine, batch-normalization, convolution, RBF, dropout, leaky-ReLU, ELU, SELU, and reshape layers where those interfaces are exercised by the tests. (~52 functions / interfaces to implement)

errors (`mlp/errors.py`): Scalar error (loss) functions used to train models, including sum-of-squared-differences, binary cross-entropy, binary cross-entropy with sigmoid, multi-class cross-entropy, and cross-entropy with softmax. Each error function requires both a forward evaluation method and a gradient method. (~10 functions to implement)

learning_rules (`mlp/learning_rules.py`): Gradient-based parameter update rules including base gradient-descent parameter initialisation/update handling, constructor-level hyperparameter validation/storage, momentum SGD, RMSProp, and Adam. The adaptive rules must also initialise and reset their optimizer state in addition to performing in-place parameter updates, and Momentum now also hollows its reset path. (~15 functions / interfaces to implement)

models (`mlp/models.py`): The single-layer and multi-layer model classes which store their wrapped layer(s), proxy wrapped-layer parameters/gradients, orchestrate forward propagation through a sequence of layers, expose a flattened `params` view over all parameterised layers, and compute gradients with respect to all model parameters via backpropagation. (~8 functions / interfaces to implement)

optimisers (`mlp/optimisers.py`): The training-loop optimiser that wires models, error functions, learning rules, and datasets together. It must initialise the learning rule from model parameters, execute batched training epochs, evaluate monitor metrics, aggregate per-epoch statistics, log them, and return full run statistics. (~6 functions / interfaces to implement)

The tests also depend on these signatures and entry points:

The following functions are called directly by the test suite. You must implement them with these exact signatures:

Layers (`mlp/layers.py`):
`AffineLayer.__init__(self, input_dim, output_dim, weights_initialiser=..., biases_initialiser=..., weights_penalty=None, biases_penalty=None)` — Initialises layer dimensions, parameter arrays, and optional penalties.
`AffineLayer.fprop(self, inputs)` — Forward pass computing `W @ x + b` for the affine transformation.
`AffineLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass computing gradients with respect to inputs.
`AffineLayer.grads_wrt_params(self, inputs, grads_wrt_outputs)` — Returns `[grads_wrt_weights, grads_wrt_biases]`. Includes penalty gradients if set.
`AffineLayer.params_penalty(self)` — Returns scalar parameter-dependent penalty (0 if no penalties set).
`AffineLayer.params` — Getter/setter pair over `[weights, biases]`.
`SigmoidLayer.fprop(self, inputs)` — Applies the logistic sigmoid element-wise.
`SigmoidLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for sigmoid.
`ReluLayer.fprop(self, inputs)` — Applies element-wise ReLU.
`ReluLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for ReLU.
`LeakyReluLayer.__init__(self, alpha=0.01)` — Stores the negative-slope coefficient.
`LeakyReluLayer.fprop(self, inputs)` — Forward pass applying leaky ReLU activation with `self.alpha` slope for negative inputs.
`LeakyReluLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for leaky ReLU.
`ELULayer.__init__(self, alpha=1.0)` — Stores the ELU alpha parameter.
`ELULayer.fprop(self, inputs)` — Forward pass applying ELU activation with `self.alpha` parameter.
`ELULayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for ELU.
`SELULayer.__init__(self)` — Initialises `self.alpha`, `self.lamda`, and the delegated `self.elu` layer.
`SELULayer.fprop(self, inputs)` — Forward pass applying SELU activation using `self.alpha` and `self.lamda` constants.
`SELULayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for SELU.
`TanhLayer.fprop(self, inputs)` — Applies element-wise hyperbolic tangent.
`TanhLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for tanh.
`SoftmaxLayer.fprop(self, inputs)` — Applies softmax to produce probability distribution per sample.
`SoftmaxLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for softmax.
`RadialBasisFunctionLayer.__init__(self, grid_dim, intervals=[[0., 1.]])` — Builds the RBF centre grid and per-dimension scales.
`RadialBasisFunctionLayer.fprop(self, inputs)` — Projects inputs to grid of radial basis functions.
`RadialBasisFunctionLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass for RBF layer.
`DropoutLayer.__init__(self, rng=None, incl_prob=0.5, share_across_batch=True)` — Stores dropout configuration and RNG.
`DropoutLayer.fprop(self, inputs, stochastic=True)` — Stochastically zeros inputs (if stochastic) or scales by inclusion probability (if deterministic).
`DropoutLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass masking gradients consistently with the forward pass.
`ReshapeLayer.__init__(self, output_shape=None)` — Stores the per-example output shape, defaulting to flattening each input.
`ReshapeLayer.fprop(self, inputs)` — Reshapes each batch element to `self.output_shape`.
`ReshapeLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Restores gradients to the original input shape.
`BatchNormalizationLayer.__init__(self, input_dim, rng=None)` — Initialises `gamma`, `beta`, `epsilon`, and caches for batch normalisation.
`BatchNormalizationLayer.fprop(self, inputs, stochastic=True)` — Forward pass of batch normalization layer, normalizing inputs and applying learnable scale/shift.
`BatchNormalizationLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass computing gradients with respect to layer inputs.
`BatchNormalizationLayer.grads_wrt_params(self, inputs, grads_wrt_outputs)` — Returns `[grads_wrt_gamma, grads_wrt_beta]`.
`BatchNormalizationLayer.params_penalty(self)` — Returns zero for this layer’s parameter-dependent penalty term.
`BatchNormalizationLayer.params` — Getter/setter pair over `[gamma, beta]`.
`ConvolutionalLayer.__init__(self, num_input_channels, num_output_channels, input_dim_1, input_dim_2, kernel_dim_1, kernel_dim_2, kernels_init=..., biases_init=..., kernels_penalty=None, biases_penalty=None)` — Initialises convolution shapes, parameter arrays, and optional penalties.
`ConvolutionalLayer.fprop(self, inputs)` — Forward pass of 2D convolution. Inputs shape: `(batch, C_in, H, W)`. Output shape: `(batch, C_out, H-kH+1, W-kW+1)`.
`ConvolutionalLayer.bprop(self, inputs, outputs, grads_wrt_outputs)` — Backward pass computing gradients with respect to inputs.
`ConvolutionalLayer.grads_wrt_params(self, inputs, grads_wrt_outputs)` — Returns `[grads_wrt_kernels, grads_wrt_biases]`.
`ConvolutionalLayer.params_penalty(self)` — Returns scalar penalty aggregated over kernels and biases.
`ConvolutionalLayer.params` — Getter/setter pair over `[kernels, biases]`.

Error functions (`mlp/errors.py`):
`SumOfSquaredDiffsError.__call__(self, outputs, targets)` — Returns scalar mean sum of squared differences.
`SumOfSquaredDiffsError.grad(self, outputs, targets)` — Returns gradient of error with respect to outputs.
`CrossEntropyError.__call__(self, outputs, targets)` — Returns scalar multi-class cross-entropy.
`CrossEntropyError.grad(self, outputs, targets)` — Returns gradient of cross-entropy.
`CrossEntropySoftmaxError.__call__(self, outputs, targets)` — Returns scalar cross-entropy with numerically stable softmax.
`CrossEntropySoftmaxError.grad(self, outputs, targets)` — Returns gradient combining softmax and cross-entropy.
`BinaryCrossEntropySigmoidError.__call__(self, outputs, targets)` — Returns scalar binary cross-entropy with sigmoid applied to outputs.
`BinaryCrossEntropySigmoidError.grad(self, outputs, targets)` — Returns gradient of binary CE with sigmoid.

Learning rules (`mlp/learning_rules.py`):
`GradientDescentLearningRule.__init__(self, learning_rate=1e-3)` — Validates and stores the scalar learning rate.
`GradientDescentLearningRule.initialise(self, params)` — stores the parameter list for in-place updates.
`GradientDescentLearningRule.update_params(self, grads_wrt_params)` — vanilla gradient descent update over all stored parameters.
`MomentumLearningRule.__init__(self, learning_rate=1e-3, mom_coeff=0.9)` — Validates and stores momentum hyperparameters.
`MomentumLearningRule.initialise(self, params)` — stores params and initialises zero-valued momentum buffers.
`MomentumLearningRule.reset(self)` — Zeroes the accumulated momentum buffers.
`MomentumLearningRule.update_params(self, grads_wrt_params)` — Momentum SGD update using `self.mom_coeff` and accumulated momentum.
`RMSPropLearningRule.__init__(self, learning_rate=1e-3, beta=0.9, epsilon=1e-8)` — Validates and stores RMSProp hyperparameters.
`RMSPropLearningRule.initialise(self, params)` — stores the parameter list and initialises zero-valued `self.mssq` state arrays matching each parameter.
`RMSPropLearningRule.reset(self)` — resets `self.mssq` to zeros.
`RMSPropLearningRule.update_params(self, grads_wrt_params)` — RMSProp parameter update using `self.beta`, `self.epsilon`, and `self.mssq` state.
`AdamLearningRule.__init__(self, learning_rate=1e-3, beta1=0.9, beta2=0.999, epsilon=1e-8)` — Validates and stores Adam hyperparameters.
`AdamLearningRule.initialise(self, params)` — stores the parameter list and initialises zero-valued `self.ms`, `self.vs`, and `self.t` state.
`AdamLearningRule.reset(self)` — resets `self.ms`, `self.vs`, and `self.t` to their initial values.
`AdamLearningRule.update_params(self, grads_wrt_params)` — Adam parameter update using `self.beta1`, `self.beta2`, `self.epsilon`, `self.ms`, `self.vs`, and `self.t` state.

Models (`mlp/models.py`):
`SingleLayerModel.__init__(self, layer)` — Stores the wrapped layer for later proxying.
`SingleLayerModel.params` — proxies the wrapped layer parameter list.
`SingleLayerModel.fprop(self, inputs)` — returns `[inputs, layer_outputs]`.
`SingleLayerModel.grads_wrt_params(self, activations, grads_wrt_outputs)` — delegates parameter-gradient computation to the wrapped layer.
`MultipleLayerModel.__init__(self, layers)` — Stores the layer sequence in model order.
`MultipleLayerModel.params` — returns a flattened list of parameters from every parameterised layer in model order.
`MultipleLayerModel.fprop(self, inputs, evaluation=False)` — Forward propagates through all layers, returning list of all activations (including inputs).
`MultipleLayerModel.grads_wrt_params(self, activations, grads_wrt_outputs)` — Backpropagates through all layers, returning list of parameter gradients.

Optimisers (`mlp/optimisers.py`):
`Optimiser.__init__(self, model, error, learning_rule, train_dataset, valid_dataset=None, data_monitors=None, notebook=False)` — Wires together the model, error, datasets, and learning-rule state.
`Optimiser.do_training_epoch(self)` — Runs one full training epoch over the training dataset.
`Optimiser.eval_monitors(self, dataset, label)` — Evaluates the configured monitors in evaluation mode and averages them over dataset batches.
`Optimiser.get_epoch_stats(self)` — Returns aggregated train/valid monitor stats for the current epoch.
`Optimiser.log_stats(self, epoch, epoch_time, stats)` — Logs a formatted per-epoch summary.
`Optimiser.train(self, num_epochs, stats_interval=5)` — Runs the full training loop and returns recorded statistics and elapsed time.
