python experiment_scripts/train_single_class.py \
    --data_root /home/davbauer/datasets/light_field_networks/cars/cars_train.hdf5 \
    --experiment_name overfit_single \
    --fit_single true \
    --num_epochs 1000 \
    --steps_til_summary 25 \
    --lr 1e-3 \
    --input_encoding positional \
    --max_num_instances 1