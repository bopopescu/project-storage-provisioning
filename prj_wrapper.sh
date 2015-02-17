#!/bin/bash

## This wrapper script makes sure Environment module is loaded,
## and lanuch a corresponding python executable in the directory
## in which the wrapper is located.

me=`basename $0`

module list > /dev/null 2>&1

if [ $? != 0 ]; then
    source /opt/_module/setup.sh
    module load cluster
    module load python
fi

source $DCCN_MOD_DIR/share/common.sh

myexec=`get_script_dir`/$(echo $m | sed 's/prj_//').py $@
