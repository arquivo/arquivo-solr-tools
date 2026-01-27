v# Purpose: Indexes text files in Solr
#!/bin/bash

COLLECTION=$1
COLLECTION_FILE=$2
HOST=$3
PORT=$4
while IFS="" read -r p || [ -n "$p" ]
do
  for collection in $p
  do
    cat $collection | while read row 
      do
        COMMIT="false"
        if  [[ "$row" == *"299"* ]]; then
          echo "commit"
          COMMIT="true"
        fi
        COMMAND=curl "http://$HOST:$PORT/solr/$COLLECTION/update/json?update.chain=script&overwrite=false&commit=$COMMIT" --data-binary @$row -H 'Content-type:application/json'
        echo $COMMAND
        ./$COMMAND
    done
  done
  wget "http://$HOST:$PORT/solr/admin/cores?wt=json" -O "$p"_metrics.json
done < $COLLECTION_FILE



