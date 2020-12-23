import collections
from typing import Dict, Any, Tuple, List, Optional

import tensorflow as tf
from dpu_utils.mlutils import Vocabulary

from .encoder import Encoder, QueryType
from utils import data_pipeline


def _try_to_queue_node(
    node: data_pipeline.TreeNode,
    queue: collections.deque,
    nodes_queued: int,
    max_nodes: int) -> bool:
  if max_nodes == -1 or nodes_queued < max_nodes:
    queue.append(node)
    return True
  else:
    return False

def _get_tree_types_seq(
    root: data_pipeline.TreeNode,
    max_nodes: int = -1) -> List[str]:
  node_types: List[str] = []
  node_queue = collections.deque()
  nodes_queued = 0
  nodes_queued += _try_to_queue_node(root, node_queue, nodes_queued, max_nodes)
  while node_queue:
    node = node_queue.popleft()
    for child in node['children']:
      if _try_to_queue_node(child, node_queue, nodes_queued, max_nodes):
        nodes_queued += 1
    node_types.append(node['type'])
  return node_types


class ASTEncoder(Encoder):
  @classmethod
  def get_default_hyperparameters(cls) -> Dict[str, Any]:
    encoder_hypers = {
      'type_vocab_size': 10000,
      'type_vocab_count_threshold': 10,
      'type_embedding_size': 128,

      'max_num_nodes': 100,
      #'max_num_children': 50
    }
    hypers = super().get_default_hyperparameters()
    hypers.update(encoder_hypers)
    return hypers

  def __init__(self, label: str, hyperparameters: Dict[str, Any], metadata: Dict[str, Any]):
    super().__init__(label, hyperparameters, metadata)
    self.node_type_shape = None
    self.children_shape = None

  @property
  def output_representation_size(self) -> int:
    return self.get_hyper('type_embedding_size')

  def make_model(self, is_train: bool = False) -> tf.Tensor:
    raise NotImplementedError()

  def embedding_layer(self, input_ids: tf.Tensor) -> tf.Tensor:
    type_embeddings = tf.get_variable(
      name='type_embeddings',
      initializer=tf.glorot_uniform_initializer(),
      shape=[len(self.metadata['type_vocab']), self.get_hyper('type_embedding_size')])
    self.__embeddings = type_embeddings

    type_embeddings = tf.nn.dropout(
      type_embeddings,
      keep_prob=self.placeholders['dropout_keep_rate'])

    return tf.nn.embedding_lookup(params=type_embeddings, ids=input_ids)

  @classmethod
  def init_metadata(cls) -> Dict[str, Any]:
    raw_metadata = super().init_metadata()
    raw_metadata['type_counter'] = collections.Counter()
    return raw_metadata

  @classmethod
  def load_metadata_from_sample(cls, data_to_load: Any, raw_metadata: Dict[str, Any], use_subtokens: bool = False,
                                mark_subtoken_end: bool = False) -> None:
    default_hypers = cls.get_default_hyperparameters()
    node_types = _get_tree_types_seq(data_to_load, default_hypers['max_num_nodes'])
    print(node_types)
    raw_metadata['type_counter'].update(node_types)

  @classmethod
  def finalise_metadata(cls, encoder_label: str, hyperparameters: Dict[str, Any],
                        raw_metadata_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    final_metadata = super().finalise_metadata(encoder_label, hyperparameters, raw_metadata_list)
    merged_type_counter = collections.Counter()
    for raw_metadata in raw_metadata_list:
      merged_type_counter += raw_metadata['type_counter']
    type_vocabulary = Vocabulary.create_vocabulary(
      tokens=merged_type_counter,
      max_size=hyperparameters['%s_type_vocab_size' % encoder_label],
      count_threshold=hyperparameters['%s_type_vocab_count_threshold' % encoder_label])
    final_metadata['type_vocab'] = type_vocabulary
    print('Total type vocabulary words:', len(final_metadata['type_vocab'].id_to_token))
    return final_metadata

  def init_minibatch(self, batch_data: Dict[str, Any]) -> None:
    super().init_minibatch(batch_data)
    batch_data['node_type_ids'] = []
    batch_data['children'] = []

  def extend_minibatch_by_sample(self, batch_data: Dict[str, Any], sample: Dict[str, Any], is_train: bool = False,
                                 query_type: QueryType = QueryType.DOCSTRING.value) -> bool:
    current_sample = {}
    current_sample['node_type_ids'] = sample[f'{self.label}_node_type_ids']
    current_sample['children'] = sample[f'{self.label}_children']
    for key, value in current_sample.items():
      if key in batch_data:
        batch_data[key].append(value)
    return False

  def get_token_embeddings(self) -> Tuple[tf.Tensor, List[str]]:
    return self.__embeddings, list(self.metadata['type_vocab'].id_to_token)