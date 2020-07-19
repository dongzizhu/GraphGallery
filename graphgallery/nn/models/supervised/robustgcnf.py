import tensorflow as tf
from tensorflow.keras import Model, Input
from tensorflow.keras.layers import Dropout, Softmax
from tensorflow.keras.optimizers import Adam
from tensorflow.keras import regularizers

from graphgallery.nn.layers import GaussionConvolutionFeature_F, GaussionConvolutionFeature_D, Sample
from graphgallery.nn.models import SupervisedModel
from graphgallery.sequence import FullBatchNodeSequence
from graphgallery import astensor, asintarr, normalize_x, normalize_adj


class RobustGCNF(SupervisedModel):
    """
        Implementation of Robust Graph Convolutional Networks (RobustGCN). 
        `Robust Graph Convolutional Networks Against Adversarial Attacks 
        <https://dl.acm.org/doi/10.1145/3292500.3330851>`
        Tensorflow 1.x implementation: <https://github.com/thumanlab/nrlweb/blob/master/static/assets/download/RGCN.zip>

        Arguments:
        ----------
            adj: shape (N, N), Scipy sparse matrix if  `is_adj_sparse=True`, 
                Numpy array-like (or matrix) if `is_adj_sparse=False`.
                The input `symmetric` adjacency matrix, where `N` is the number 
                of nodes in graph.
            x: shape (N, F), Scipy sparse matrix if `is_x_sparse=True`, 
                Numpy array-like (or matrix) if `is_x_sparse=False`.
                The input node feature matrix, where `F` is the dimension of features.
            labels: Numpy array-like with shape (N,)
                The ground-truth labels for all nodes in graph.
            norm_adj_rate (List of float scalar, optional): 
                The normalize rate for adjacency matrix `adj`. 
                (default: :obj:`[-0.5, -1]`, i.e., two normalized `adj` with rate `-0.5` 
                and `-1.0`, respectively) 
            norm_x_type (String, optional): 
                How to normalize the node feature matrix. See `graphgallery.normalize_x`
                (default :str: `l1`)
            device (String, optional): 
                The device where the model is running on. You can specified `CPU` or `GPU` 
                for the model. (default: :str: `CPU:0`, i.e., running on the 0-th `CPU`)
            seed (Positive integer, optional): 
                Used in combination with `tf.random.set_seed` & `np.random.seed` & `random.seed`  
                to create a reproducible sequence of tensors across multiple calls. 
                (default :obj: `None`, i.e., using random seed)
            name (String, optional): 
                Specified name for the model. (default: :str: `class.__name__`)

    """

    def __init__(self, adj, x, labels, norm_adj_rate=[-0.5, -1], norm_x_type='l1',
                 device='CPU:0', seed=None, name=None, **kwargs):

        super().__init__(adj, x, labels, device=device, seed=seed, name=name, **kwargs)

        self.norm_adj_rate = norm_adj_rate
        self.norm_x_type = norm_x_type
        self.preprocess(adj, x)

    def preprocess(self, adj, x):
        super().preprocess(adj, x)
        # check the input adj and x, and convert them into proper data types
        adj, x = self._check_inputs(adj, x)

        if self.norm_adj_rate:
            adj = normalize_adj([adj, adj], self.norm_adj_rate)    # [adj_1, adj_2]

        if self.norm_x_type:
            x = normalize_x(x, norm=self.norm_x_type)

        with tf.device(self.device):
            self.x_norm, self.adj_norm = astensor([x, adj])

    def build(self, hiddens=[64], activations=['relu'], use_bias=False, dropout=0.5, 
              lr=0.01, l2_norm=5e-4, kl=5e-4, gamma=1., ensure_shape=True):

        assert len(hiddens) == len(activations), "The number of hidden units and " \
            "activation functions should be the same."

        with tf.device(self.device):

            x = Input(batch_shape=[None, self.n_features], dtype=self.floatx, name='features')
            adj = [Input(batch_shape=[None, None], dtype=self.floatx, sparse=True, name='adj_matrix_1'),
                   Input(batch_shape=[None, None], dtype=self.floatx, sparse=True, name='adj_matrix_2')]
            index = Input(batch_shape=[None],  dtype=self.intx, name='index')

            h = Dropout(rate=dropout)(x)
            mean, var = GaussionConvolutionFeature_F(hiddens[0], gamma=gamma, kl=kl,
                                                     use_bias=use_bias,
                                                     activation=activations[0],
                                                     kernel_regularizer=regularizers.l2(l2_norm))([h, *adj])

            # additional layers (usually unnecessay)
            for hid, activation in zip(hiddens[1:], activations[1:]):
                mean = Dropout(rate=dropout)(mean)
                var = Dropout(rate=dropout)(var)
                mean, var = GaussionConvolutionFeature_D(hid, gamma=gamma, use_bias=use_bias, activation=activation)([mean, var, *adj])

            mean = Dropout(rate=dropout)(mean)
            var = Dropout(rate=dropout)(var)
            mean, var = GaussionConvolutionFeature_D(self.n_classes, gamma=gamma, use_bias=use_bias)([mean, var, *adj])
            h = Sample()(mean, var)
            if ensure_shape:
                h = tf.ensure_shape(h, [self.n_nodes, self.n_classes])
            h = tf.gather(h, index)
            output = Softmax()(h)

            model = Model(inputs=[x, *adj, index], outputs=output)
            model.compile(loss='sparse_categorical_crossentropy', optimizer=Adam(lr=lr), metrics=['accuracy'])

            self.set_model(model)

    def train_sequence(self, index):
        index = asintarr(index)
        labels = self.labels[index]
        with tf.device(self.device):
            sequence = FullBatchNodeSequence([self.x_norm, *self.adj_norm, index], labels)
        return sequence

    def predict(self, index):
        super().predict(index)
        index = asintarr(index)

        with tf.device(self.device):
            index = astensor(index)
            logit = self.model.predict_on_batch([self.x_norm, *self.adj_norm, index])

        if tf.is_tensor(logit):
            logit = logit.numpy()
        return logit
