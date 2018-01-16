import tensorflow as tf
from conv_lstm_cell import ConvLSTMCell
import os

# network architecture definition
NCHANNELS = 1
CONV1 = 64
CONV2 = 64
CONV3 = 64
CONV4 = 32
CLSTM1 = 32
CLSTM2 = 32
CLSTM3 = 32
DECONV1 = 1
WIDTH = 227
HEIGHT = 227


class Experiment(object):
    def __init__(self, tvol, alpha, batch_size, lambd):
        self.tvol = tvol
        self.x_ = tf.placeholder(tf.float32, [None, self.tvol, HEIGHT, WIDTH, NCHANNELS])
        self.phase = tf.placeholder(tf.bool, name='is_training')

        self.batch_size = batch_size
        w_init = tf.contrib.layers.xavier_initializer_conv2d()
        self.params = {
            "c_w1": tf.get_variable("c_weight1", shape=[7, 7, NCHANNELS, CONV1], initializer=w_init),
            "c_b1": tf.Variable(tf.constant(0.01, dtype=tf.float32, shape=[CONV1]), name="c_bias1"),
            "c_w2": tf.get_variable("c_weight2", shape=[5, 5, CONV1, CONV2], initializer=w_init),
            "c_b2": tf.Variable(tf.constant(0.01, dtype=tf.float32, shape=[CONV2]), name="c_bias2"),
            "c_w3": tf.get_variable("c_weight3", shape=[3, 3, CONV2, CONV3], initializer=w_init),
            "c_b3": tf.Variable(tf.constant(0.01, dtype=tf.float32, shape=[CONV3]), name="c_bias3"),
            "c_w4": tf.get_variable("c_weight4", shape=[3, 3, CONV3, CONV4], initializer=w_init),
            "c_b4": tf.Variable(tf.constant(0.01, dtype=tf.float32, shape=[CONV4]), name="c_bias4"),
            "c_w_1": tf.get_variable("c_weight_1", shape=[3, 3, CLSTM3, DECONV1], initializer=w_init),
            "c_b_1": tf.Variable(tf.constant(0.01, dtype=tf.float32, shape=[DECONV1]), name="c_bias_1")
        }

        self.conved = self.spatial_encoder(self.x_)
        self.convLSTMed = self.temporal_encoder_decoder(self.conved)
        self.y = self.spatial_decoder(self.convLSTMed)
        self.y = tf.reshape(self.y, shape=[-1, self.tvol, HEIGHT, WIDTH, NCHANNELS])

        self.per_frame_recon_errors = tf.reduce_sum(tf.square(self.x_ - self.y), axis=[2, 3, 4])

        self.reconstruction_loss = 0.5 * tf.reduce_mean(self.per_frame_recon_errors)
        self.vars = tf.trainable_variables()
        self.regularization_loss = tf.add_n([tf.nn.l2_loss(v) for v in self.vars if 'bias' not in v.name])
        self.loss = self.reconstruction_loss + lambd * self.regularization_loss
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            self.optimizer = tf.train.AdamOptimizer(alpha, epsilon=1e-6).minimize(self.loss)

        self.saver = tf.train.Saver()

        self.sess = tf.InteractiveSession()
        self.sess.run(tf.global_variables_initializer())

    @staticmethod
    def conv2d(x, w, b, activation=tf.nn.relu, strides=1, phase=True):
        """
        Build a convolutional layer
        :param x: input
        :param w: filter
        :param b: bias
        :param activation: activation func
        :param strides: the stride when filter is scanning through image
        :param phase: training phase or not
        :return: a convolutional layer representation
        """
        x = tf.nn.conv2d(x, w, strides=[1, strides, strides, 1], padding='VALID')
        x = tf.nn.bias_add(x, b)
        x = tf.contrib.layers.batch_norm(x, center=True, scale=True, is_training=phase)
        return activation(x)

    @staticmethod
    def deconv2d(x, w, b, out_shape, activation=tf.nn.relu, strides=1, phase=True, last=False):
        """
        Build a deconvolutional layer composed of NN-resizing + convolution
        :param x: input
        :param w: filter
        :param b: bias
        :param out_shape: output height and width after NN-resizing
        :param activation: activation func
        :param strides: the stride when filter is scanning
        :param phase: training phase or not
        :param last: last layer of the network or not
        :return: a deconvolutional layer representation
        """
        x = tf.image.resize_images(x, out_shape, method=tf.image.ResizeMethod.NEAREST_NEIGHBOR)
        x = tf.nn.conv2d(x, w, strides=[1, strides, strides, 1], padding='SAME')
        x = tf.nn.bias_add(x, b)
        if last:
            return x
        else:
            x = tf.contrib.layers.batch_norm(x, center=True, scale=True, is_training=phase)
            return activation(x)

    def spatial_encoder(self, x):
        """
        Build a spatial encoder that performs convolutions
        :param x: tensor of input image of shape (batch_size, self.tvol, HEIGHT, WIDTH, NCHANNELS)
        :return: convolved representation of shape (batch_size * self.tvol, h, w, c)
        """
        _, _, h, w, c = x.get_shape().as_list()
        x = tf.reshape(x, shape=[-1, h, w, c])
        conv1 = self.conv2d(x, self.params['c_w1'], self.params['c_b1'], activation=tf.nn.relu, strides=2,
                            phase=self.phase)
        conv2 = self.conv2d(conv1, self.params['c_w2'], self.params['c_b2'], activation=tf.nn.relu, strides=2,
                            phase=self.phase)
        conv3 = self.conv2d(conv2, self.params['c_w3'], self.params['c_b3'], activation=tf.nn.relu, strides=1,
                            phase=self.phase)
        conv4 = self.conv2d(conv3, self.params['c_w4'], self.params['c_b4'], activation=tf.nn.relu, strides=1,
                            phase=self.phase)
        return conv4

    def temporal_encoder_decoder(self, x):
        """
        Build a temporal encoder-decoder network that uses convLSTM layers to perform sequential operation
        :param x: convolved representation of input volume of shape (batch_size * self.tvol, h, w, c)
        :return: convLSTMed representation (batch_size, self.tvol, h, w, c)
        """
        _, h, w, c = x.get_shape().as_list()
        x = tf.reshape(x, shape=[-1, self.tvol, h, w, c])
        x = tf.unstack(x, axis=1)
        num_filters = [CLSTM1, CLSTM2, CLSTM3]
        filter_sizes = [[3, 3] for _ in xrange(len(num_filters))]
        cell = tf.nn.rnn_cell.MultiRNNCell(
            [ConvLSTMCell(shape=[h, w], num_filters=num_filters[i], filter_size=filter_sizes[i], layer_id=i)
             for i in xrange(len(num_filters))])
        states_series, _ = tf.nn.static_rnn(cell, x, dtype=tf.float32)
        output = tf.transpose(tf.stack(states_series, axis=0), [1, 0, 2, 3, 4])
        return output

    def spatial_decoder(self, x):
        """
        Build a spatial decoder that performs deconvolutions on the input
        :param x: tensor of some transformed representation of input of shape (batch_size, self.tvol, h, w, c)
        :return: deconvolved representation of shape (batch_size * self.tvol, HEIGHT, WEIGHT, NCHANNELS)
        """
        _, _, h, w, c = x.get_shape().as_list()
        x = tf.reshape(x, shape=[-1, h, w, c])
        deconv1 = self.deconv2d(x, self.params['c_w_1'], self.params['c_b_1'], [HEIGHT, WIDTH], activation=tf.nn.relu,
                                strides=1, phase=self.phase, last=True)
        return deconv1

    def get_loss(self, x, is_training):
        return self.loss.eval(feed_dict={self.x_: x, self.phase: is_training}, session=self.sess)

    def step(self, x, is_training):
        self.sess.run(self.optimizer, feed_dict={self.x_: x, self.phase: is_training})

    def get_recon_errors(self, x, is_training):
        return self.per_frame_recon_errors.eval(feed_dict={self.x_: x, self.phase: is_training},
                                                session=self.sess)

    def save_model(self, path):
        self.saver.save(self.sess, os.path.join(path, "model.ckpt"))

    def restore_model(self, path):
        self.saver.restore(self.sess, os.path.join(path, "model.ckpt"))

    def batch_reconstruct(self, x):
        return self.y.eval(feed_dict={self.x_: x, self.phase: False}, session=self.sess)

    def batch_train(self, xbatch):
        self.step(xbatch, is_training=True)
        return self.get_loss(xbatch, is_training=False)
