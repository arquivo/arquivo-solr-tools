#!/bin/bash

# Solr Collection Setup Script
# Tested on node p82 with Solr 9.6.1

set -e

# Configuration
SOLR_BIN=/data/solr9/solr-9.6.1/bin/solr
ZK_HOST=p44.arquivo.pt
ZK_PORT=2201
SOLR_PORT=2200
NUM_SHARDS=12
REPLICATION_FACTOR=2
MAX_SHARDS_PER_NODE=12
CONFIG_NAME=pages  # Use 'images' for image collections
COLLECTION_NAME=page

echo "================================================"
echo "Solr Collection Setup"
echo "================================================"
echo "Collection Name: $COLLECTION_NAME"
echo "Config Name: $CONFIG_NAME"
echo "Shards: $NUM_SHARDS"
echo "Replication Factor: $REPLICATION_FACTOR"
echo "================================================"

# Clone repository if not already present
if [ ! -d "arquivo-solr-tools" ]; then
    echo "Cloning arquivo-solr-tools repository..."
    git clone https://github.com/arquivo/arquivo-solr-tools.git
fi

cd arquivo-solr-tools/pages/init/

# Upload configuration to Zookeeper
echo "Uploading configuration to Zookeeper..."
$SOLR_BIN zk upconfig -n $CONFIG_NAME -d solr-configset/$CONFIG_NAME -z $ZK_HOST:$ZK_PORT

# Create collection
echo "Creating collection..."
curl -s "http://$ZK_HOST:$SOLR_PORT/solr/admin/collections?action=CREATE&name=$COLLECTION_NAME&numShards=$NUM_SHARDS&replicationFactor=$REPLICATION_FACTOR&maxShardsPerNode=$MAX_SHARDS_PER_NODE&collection.configName=$CONFIG_NAME"

echo ""
echo "================================================"
echo "Collection '$COLLECTION_NAME' created successfully!"
echo "================================================"
