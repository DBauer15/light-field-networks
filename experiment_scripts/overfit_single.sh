python experiment_scripts/train_single_class.py \
    --data_root $HOME/datasets/light_field_networks/chairs/chairs_train.hdf5 \
    --experiment_name $1 \
    --fit_single true \
    --num_epochs 1000 \
    --steps_til_summary 25 \
    --lr 1e-3 \
    --input_encoding positional \
    --max_num_instances 1