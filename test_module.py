import numpy as np
from matplotlib import pyplot as plt
import torch

from dataloader.constants import *
from dataloader.gnn_setup import *

def generate_data(data_size):
    feat_list, adj_list, label_list = [], [], []
    grid_list  = []
    robot_pos_list  = []
    for _ in range(data_size):
        grid = get_reward_grid(height=HEIGHT, width=WIDTH, reward_thresh=REWARD_THRESH)
        robot_pos, adj_mat = get_initial_pose(grid, comm_range=COMM_RANGE)

        cent_act, cent_rwd = centralized_greedy_action_finder(grid, robot_pos, fov=FOV)
        rand_act, rand_rwd = random_action_finder(grid, robot_pos, 1000)

        if cent_rwd > rand_rwd:
            action_vec = cent_act
        else:
            action_vec = rand_act
        
        feat_vec = get_features(grid, robot_pos, fov=FOV, step=STEP, target_feat_size=NUM_TGT_FEAT, robot_feat_size=NUM_ROB_FEAT)

        feat_list.append(feat_vec)
        adj_list.append(adj_mat)
        
        action_one_hot = np.zeros((NUM_ROBOT, len(DIR_LIST)), dtype=np.uint8)
        action_one_hot[np.arange(NUM_ROBOT), action_vec] = 1
        label_list.append(action_one_hot)

        grid_list.append(grid.copy())
        robot_pos_list.append(robot_pos.copy())
    
    return [np.array(feat_list), np.array(adj_list), np.array(label_list), grid_list, robot_pos_list]

def calculate_test_reward(grid, robot_pos, action_list):
    """
    Function to calculate the reward calculated by all the robots based on an action vector.
    For this we first update locations of each robot, then create a mask which has 1s only around the new robots locations (square of side (2*FOV+1) for each robot) 
    
    Parameters
    ----------
        grid: 2D grid containing rewards
        robot_pos: Current position for each robot on the grid (NUM_ROBOTx2 size vector)
        action_list: List of action for each robot
    
    Returns
    -------
        total_reward: Total reward calculated by the robots using action_list (the action vector)
    """
    # Convert the integer actions to 2D vector of location differences using DIR_DICT dictionary
    act = np.array([DIR_DICT[k] for k in action_list])
    # Calcuate new locations for each robot
    new_pos = robot_pos + act
    # Make sure that the new locatiosn are within the grid 
    new_pos = new_pos.clip(min=0, max=GRID_SIZE-1)
    
    # Initialize a mask of same shape as grid
    mask = np.zeros(grid.shape, dtype=int)
    
    # iterate over each robot position
    for c_pos, n_pos in zip(robot_pos, new_pos):
        # Set the values to 1 in the mask at each robot's fov
        # also make sure that the indices do not go out of grid
        
        # Calculate the bounding box ranges for the box generated by robot moving from the current location (c_pos) to new location (n_pos)
        # This box has a padding of size FOV on each size
        r_lim_lef = max(0, min(c_pos[0]-FOV, n_pos[0]-FOV))
        c_lim_top = max(0, min(c_pos[1]-FOV, n_pos[1]-FOV))
        r_lim_rgt = min(max(c_pos[0]+FOV+1, n_pos[0]+FOV+1), GRID_SIZE)
        c_lim_bot = min(max(c_pos[1]+FOV+1, n_pos[1]+FOV+1), GRID_SIZE)
        
        # Set the locations withing mask (i.e. witing robot's vision when it moved) to 1
        mask[r_lim_lef:r_lim_rgt, c_lim_top:c_lim_bot] = 1
        
    # Find total reward as number of 1s in the masked grid
    total_reward = np.sum(grid * mask)
    
    return total_reward, mask

def plot_function(grid, robot_pos, gt_act, predict_act, random_act):
    gt_rwd, gt_mask = calculate_test_reward(grid, robot_pos, gt_act)
    pred_rwd, pred_mask = calculate_test_reward(grid, robot_pos, predict_act)
    rndm_rwd, rndm_mask = calculate_test_reward(grid, robot_pos, random_act)

    plt.figure(figsize=(12,6));
    plt.subplot(1,3,1);
    plt.imshow(gt_mask);
    plt.title(f'GT: {gt_rwd}', fontsize= 20)
    plt.subplot(1,3,2);
    plt.imshow(pred_mask);
    plt.title(f'Prediction: {pred_rwd}', fontsize= 20)
    plt.subplot(1,3,3);
    plt.imshow(rndm_mask);
    plt.title(f'Random: {rndm_rwd}', fontsize= 20)
    # plt.suptitle('Reward calculation', fontsize= 20);

def get_accuracy(config, agent):
    dataloader = agent.data_loader.test_loader
    gt_list_long, pred_list_long = [], []

    agent.model.eval();

    for batch_idx, (batch_input, batch_GSO, batch_target) in enumerate(dataloader):
        inputGPU = batch_input.to(config.device)
        gsoGPU = batch_GSO.to(config.device)
        # gsoGPU = gsoGPU.unsqueeze(0)
        targetGPU = batch_target.to(config.device)
        batch_targetGPU = targetGPU.permute(1, 0, 2)
        agent.optimizer.zero_grad()

        # loss
        loss_validStep = 0

        # model
        agent.model.addGSO(gsoGPU)
        predict = agent.model(inputGPU)

        gt_list_long.append(targetGPU.detach().cpu().numpy())
        pred_list_long.append(np.array([p.detach().cpu().numpy() for p in predict]).transpose(1,0,2))

    np.concatenate(gt_list_long, axis=0).shape, np.concatenate(pred_list_long, axis=0).shape
    gt_idxs = np.concatenate(gt_list_long, axis=0).argmax(axis=2)
    pred_idxs = np.concatenate(pred_list_long, axis=0).argmax(axis=2)

    accuracy = (gt_idxs == pred_idxs).sum()/(len(gt_idxs)*NUM_ROBOT)
    return accuracy

def main(config, agent, num_example=5):
    
    temp_list = generate_data(num_example)
    print(config.tgt_feat, config.rbt_feat)
    
    numFeature = (config.tgt_feat + config.rbt_feat )
    featlist, adjlist, tgtlist = [], [], []

    feat, adj, tgt, _, _ = temp_list
    feat_reshaped = feat[:,:,:numFeature,:].reshape(feat.shape[0], feat.shape[1], numFeature*2)
    featlist.append(feat_reshaped)
    adjlist.append(adj)
    tgtlist.append(tgt)

    features_tensor = torch.FloatTensor(np.concatenate(featlist, axis=0))
    adj_mat_tensor  = torch.FloatTensor(np.concatenate(adjlist, axis=0))
    targets_tensor  = torch.LongTensor(np.concatenate(tgtlist, axis=0))


    inputGPU = features_tensor.to(config.device)
    gsoGPU = adj_mat_tensor.to(config.device)
    # gsoGPU = gsoGPU.unsqueeze(0)
    targetGPU = targets_tensor.to(config.device)
    batch_targetGPU = targetGPU.permute(1, 0, 2)

    agent.model.eval();

    agent.optimizer.zero_grad()
    agent.model.addGSO(gsoGPU)
    predict = agent.model(inputGPU)

    predict_np = np.array([p.detach().cpu().numpy() for p in predict])
    predict_ind = predict_np.argmax(axis=2)

    _, _, gt_act_list, grid_list, robot_pos_list = temp_list 
    gt_act_list = gt_act_list.argmax(axis=2)

    random_acts = np.random.randint(low=0,high=len(DIR_DICT.keys()), size=(len(gt_act_list), NUM_ROBOT))

    return grid_list, robot_pos_list, gt_act_list, predict_ind, random_acts

