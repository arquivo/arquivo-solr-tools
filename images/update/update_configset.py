import sys
import os
from kazoo.client import KazooClient

# Usage: python3 update_configset.py <zookeeper_host:port> <configset_dir>
# Example: python3 update_configset.py p76.arquivo.pt:3201 ../init/solr-configset/images/conf

zk_host = sys.argv[1]
configset_dir = sys.argv[2]
zk_path = "/configs/images"

zk = KazooClient(hosts=zk_host)
zk.start()

for root, dirs, files in os.walk(configset_dir):
    for filename in files:
        local_path = os.path.join(root, filename)
        relative_path = os.path.relpath(local_path, configset_dir)
        node_path = zk_path + "/" + relative_path.replace(os.sep, "/")

        with open(local_path, "rb") as f:
            data = f.read()

        if zk.exists(node_path):
            zk.set(node_path, data)
        else:
            zk.create(node_path, data, makepath=True)

        print(f"Updated {node_path}")

zk.stop()
print("Done.")
