import sys

import librosa
import librosa.filters
import numpy as np
import lws

from vocoderGANPatches import VocoderGAN
from util import override_model_attrs

fn = 'real_sc09.wav'


nsteps = int(sys.argv[1])
fext = str(sys.argv[2])
_gen_filter_all = str(sys.argv[3]) # True, False
_gen_filter_last = str(sys.argv[4]) # True, False

spec = 'wavenet_vocoder_mel'
use_noise = True
learn_noise = False
opt = 'l1'


import numpy as np
from scipy.io.wavfile import read as wavread, write as wavwrite
import tensorflow as tf

fs, _x = wavread(fn)
_x = np.reshape(_x, [50, -1])
_x = _x[0, :16384]
_x = _x.astype(np.float32)
_x /= 32767.

print ("Shape", _x.shape)

x = tf.constant(_x, dtype=tf.float32)

dec_input = []

# STFT
if spec == 'mag':
  X = tf.contrib.signal.stft(x, 128, 256, pad_end=True)
  X_mag = tf.abs(X)
  X_spec = X_mag[:, :-1]
elif spec == 'wavenet_vocoder_mel':
  # Adapted from https://github.com/r9y9/wavenet_vocoder/blob/master/audio.py
  # Only difference from out-of-box is that they use 22kHz
  nfft = 1024
  nhop = 256
  
  # TODO: Figure out what to do about center-vs-left-padding (lws uses center padding, decoder probably should as well)
  _X = lws.lws(nfft, nhop, mode='speech').stft(_x)[3:]
  _X_mag = np.abs(_X)

  _mel = librosa.filters.mel(16000, nfft, fmin=125, fmax=7600, n_mels=80)
  _X_mel = np.dot(_X_mag, _mel.T)

  min_level_db = -100
  ref_level_db = 20
  min_level = np.exp(min_level_db / 20. * np.log(10))
  _X_mel_db = 20. * np.log10(np.maximum(min_level, _X_mel)) - ref_level_db

  #assert _X_mel_db.max() <= 0 and _X_mel_db.min() - min_level_db >= 0
  _X_mel_dbnorm = np.clip((_X_mel_db - min_level_db) / -min_level_db, 0, 1)

  X_spec = tf.constant(_X_mel_dbnorm, dtype=tf.float32)
else:
  raise ValueError()
X_spec = tf.stop_gradient(X_spec)
dec_input.append(X_spec)

# Noise
opt_vars = []
if use_noise:
  if learn_noise:
    noise = tf.get_variable('noise', [64, 64])
    opt_vars.append(noise)
  else:
    z = tf.random.normal([1, 64], dtype=tf.float32)
    noise = z * tf.constant(1.0, shape=[64, 64])
  dec_input.append(noise)

# dec_input is list of [64, 64]
# transform to [1, 64, 1, 64*len(dec_input)]
X_spec = X_spec[tf.newaxis, :, tf.newaxis, :]
X_spec = tf.transpose(X_spec, [0, 1, 3, 2])
noise = noise[tf.newaxis, :, tf.newaxis, :]

print("X_spec", X_spec)
print("noise", noise)
vg = VocoderGAN('TRAIN')

vg, summary = override_model_attrs(vg, 
  "gen_filter_all={},gen_filter_last={}".format(_gen_filter_all, _gen_filter_last))
print('-' * 80)
print(summary)
print('-' * 80)

with tf.variable_scope('Dec'):
  Dec_x = vg.build_generator(X_spec, noise)
  print("Dec_x", Dec_x)
opt_vars.extend(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='Dec'))
for var in opt_vars:
  print(var)

if opt == 'l1':
  loss = tf.reduce_mean(tf.abs(Dec_x[0,:,0,0] - x))
elif opt == 'l2':
  loss = tf.reduce_mean(tf.square(Dec_x[0,:,0,0] - x))
else:
  raise ValueError()

opt = tf.train.AdamOptimizer()
step = tf.train.get_or_create_global_step()
train = opt.minimize(loss, var_list=opt_vars, global_step=step)

with tf.Session() as sess:
  sess.run(tf.global_variables_initializer())

  for i in range(nsteps):
    _loss, _ = sess.run((loss, train))
    if i % 100 == 0:
      print("Step {} of {}. Loss = {}".format(i, nsteps, _loss))


  _Dec_x = sess.run(Dec_x)[0,:,0,0]
  _Dec_x *= 32767.
  _Dec_x = np.clip(_Dec_x, -32768., 32767.)
  _Dec_x = _Dec_x.astype(np.int16)
  print("Final Loss = {}".format(_loss) )
  wavwrite('OverfitExperiments/overfit_{}_{}_l={}.wav'.format(nsteps, fext, int(_loss * 10000) ), fs, _Dec_x)