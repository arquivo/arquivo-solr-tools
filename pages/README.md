# Page index script description

## init

Used to init a SolrCloud document index.

- `send_config_set.sh`: sends the `pages` configset to Zookeeper
- `init_collection.sh`: creates a collection with `pages` config

- `solr-configset/`: contains the necessary information to make a SolrCloud instance ready to have an Arquivo.pt page search index. They are used in the `solr-cloud-page-index` Ansible roles, which is the prefered way for creating an image index. Relevant files include:
  - `pages/conf/update-script.js`: script that takes care of deduplication of images across collections. It performs the same role as the `DocumentDupDigestMergerJob` job from the [image and page search indexer](https://github.com/arquivo/image-search-indexing).
  - `pages/conf/managed-schema`: Solr schema for the current page index 


## post

Used to send documents to Solr.

TODO

## test

TODO

## update

TODO
