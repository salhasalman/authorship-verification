import tensorflow as tf
import pandas as pd
import numpy as np
import os
from copy import copy
import pickle
from nltk.tokenize import RegexpTokenizer
w_tokenizer = RegexpTokenizer('\w+')

sequence_length = 256
num_refs = 4
hidden_size = 128

dic = pickle.load(open('dictionary', 'rb'))
stored_embeddings = pickle.load(open('embeddings', 'rb'))


def create_data(verbose = False):
    """
    Input:
        min_words: min words in article for it to be in data
        num_refs: number of texts to use as references to compare to candidate text
    Output: CSV file containing filenames of texts to serve as features and labels corresponding to whether a candidate is from the same author 
    """
    
    parent_dir = 'data/Reuters-50'
    authors = os.listdir(parent_dir)
    count = len(authors)
    c = 0
    
    # Discard texts shorter than min length. Do first seperately so we don't have to check length again when choosing candidates from other authors
    data = pd.DataFrame({}, columns = ['author'] + ['ref'+str(i) for i in range(num_refs)] + ['candidate', 'target'])
    texts = {}
    for author in authors:
        all_texts = os.listdir(os.path.join(parent_dir,author))
        long_texts = []
        for text in all_texts:
            with open(os.path.join(parent_dir,author,text)) as f:
                content = f.read()
                wc = len(w_tokenizer.tokenize(content))
                if wc > sequence_length:
                    long_texts.append(text)
        texts[author] = long_texts

    for author in authors:
        for text in texts[author]:
            references = copy(texts[author])
            references.remove(text)
            for _ in range(4):
                chosen_refs = np.random.choice(references, num_refs, replace=False)
                candidate = [os.path.join(author, text)]
                hit = [author] + list(map(lambda x: os.path.join(author, x), chosen_refs)) + candidate + [1]
                data = data.append(dict(zip(data.columns, hit)), ignore_index=True)

        other_authors = copy(authors)
        other_authors.remove(author)
        
        for other_author in other_authors:
            candidates = np.random.choice(texts[other_author], 15, replace=False)
            for candidate in candidates:
                chosen_refs = np.random.choice(texts[author], num_refs, replace=False)
                chosen_candidate = os.path.join(other_author, candidate)
                miss = [author] + list(map(lambda x: os.path.join(author, x), chosen_refs)) + [chosen_candidate] + [0]
                data = data.append(dict(zip(data.columns, miss)), ignore_index=True)
        c+=1
        if verbose:
            print('Done {}/{}'.format(c, count))
    
    data = data.sample(frac=1)
    data.to_csv('train-data/data.csv', index=False)
    return data
    

def process_file(filename):
    with open(os.path.join('data/Reuters-50', filename)) as f:
        content = f.read().lower()
        words = w_tokenizer.tokenize(content)
        wc = len(words)
        start = np.random.choice(wc - sequence_length)
        
        idx = list(map(lambda x: dic.get(x, 0), words[start:start+sequence_length]))
        return np.array(idx)


def generate_batch(data, batch_num, size):
    subset = data.iloc[batch_num*size : batch_num*size + size,:]
    refs = copy(subset.iloc[:,1:-2])
    candidates = copy(subset.iloc[:,-2])
    
    refs = refs.applymap(process_file).values
    candidates = candidates.apply(process_file).values
    targets = subset.iloc[:,-1].values
    
    return refs, candidates, targets


def get_accuracy(outs, labels):
    sigmoids = tf.sigmoid(outs)
    preds = tf.round(sigmoids)
    score = tf.equal(outs, preds)
    accuracy = tf.reduce_mean(tf.cast(score, tf.float32))
    return accuracy


def train(data, epochs = 10, batch_size = 64):
    graph = tf.Graph()
    with graph.as_default():
        train_refs = [tf.placeholder(tf.int32, shape = (None, sequence_length)) for _ in range(num_refs)]
        train_candidates = tf.placeholder(tf.int32, shape = (None, sequence_length))
        train_targets = tf.placeholder(tf.float32, shape = (None,1))
        embeddings = tf.constant(stored_embeddings, dtype = tf.float32)
        refs_embed = [tf.nn.embedding_lookup(embeddings, train_ref) for train_ref in train_refs]
        candidate_embed = tf.nn.embedding_lookup(embeddings, train_candidates)

        initializer = tf.initializers.truncated_normal()
        rnn_cell = tf.contrib.rnn.BasicLSTMCell(hidden_size, activation = tf.nn.sigmoid)
        LSTM_refs_outs = [tf.nn.dynamic_rnn(rnn_cell, ref, dtype = tf.float32) for ref in refs_embed]
        LSTM_candidate_outs = tf.nn.dynamic_rnn(rnn_cell, candidate_embed, dtype = tf.float32)

        last_states_refs = [LSTM_out[1].h for LSTM_out in LSTM_refs_outs]
        mean_states_refs = tf.reduce_mean(last_states_refs, axis = 0)
        last_states_candidate = LSTM_candidate_outs[1].h
        all_states = tf.concat([mean_states_refs, last_states_candidate], axis = 1)

        layer1 = tf.layers.dense(all_states, 64, kernel_initializer = initializer, activation = tf.nn.relu)
        outputs = tf.layers.dense(layer1, 1, kernel_initializer = initializer, activation = None)

        loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(labels=train_targets, logits=outputs))
        optimizer = tf.train.AdamOptimizer()
        gvs = optimizer.compute_gradients(loss)
        capped_gvs = [(tf.clip_by_value(grad, -1., 1.), var) for grad, var in gvs]
        train_op = optimizer.apply_gradients(capped_gvs)    

        accuracy = get_accuracy(outputs, train_targets)

    num_batches = len(data) // batch_size
    with tf.Session(graph = graph) as sess:
        tf.global_variables_initializer().run()
        cum_loss = 0
        for epoch in range(epochs):
            for batch in range(num_batches):
                batch_refs, batch_candidates, batch_targets = generate_batch(data, batch, batch_size)
                feed_dict = {t_ref: b_ref for t_ref, b_ref in zip(train_refs, batch_refs)}
                feed_dict.update({train_candidates: batch_candidates, train_targets: batch_targets})
                _, l = sess.run([train_op, loss], feed_dict=feed_dict)
                cum_loss += l
                if (batch + 1) % 100 == 0:
                    print('Batch {} of {}. Average loss over past 100 batches: {:0.3f}'.format(batch + 1, num_batches, cum_loss/100))
                    cum_loss = 0
            print('Finished epoch {}\n'.format(epoch+1))
            all_refs, all_candidates, all_targets = generate_batch(data, 0, len(data))
            acc_feed_dict = {t_ref: b_ref for t_ref, b_ref in zip(train_refs, all_refs)}
            acc_feed_dict.update({train_candidates: all_candidates, train_targets: all_targets})
            print('The accuracy is {:.1%}'.format(get_accuracy(outs, targets).eval(feed_dict=acc_feed_dict)))
    
if __name__ == "__main__":
    if os.path.exists('train-data/data.csv'):
        data = pd.read_csv('train-data/data.csv')
    else:
        data = create_data()
    
    train(data)
    