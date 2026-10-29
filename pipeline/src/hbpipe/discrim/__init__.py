"""Phase 3: the discriminator, the measuring stick for how human the bot looks (spec, Component D).

    eval_set        clips of human and bot play per action set, balanced, with session and day
    features        hand-built statistics per clip, in groups (motion, keys, events, mouse)
    evaluate        session-split cross-validation, accuracy and ROC AUC with bootstrap intervals
                    over sessions, per-feature distances
    feature_model   gradient boosting over the clip statistics (model 1)
    sequence_model  a 1D CNN over the per-tick streams (model 2)
    decision_model  a sequence model over a mining job's breaks (model 3)
    report          runs them all from scratch and writes hb-data/discrim/<run>/

Every run trains fresh models: a discriminator used to tune the bot is never the one that judges
it (spec, leakage rules).
"""
