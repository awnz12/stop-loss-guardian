// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

/// @title MockPositionVault — mock protected positions on the destination chain.
/// @notice Stands in for a real trading position (DEX position, perp, vault share). Users
///         open mock positions; when the stop-loss callback arrives, the StopLossExecutor —
///         and only the executor — can close every open position in the affected token.
/// @dev Demo scale by design: holders per token are kept in a small array and exited in a
///      loop. A production version would page the exits or key them per user.
contract MockPositionVault {
    struct Position {
        uint256 amount;
        bool open;
    }

    address public owner;

    /// @notice The only address allowed to force-exit positions (the StopLossExecutor).
    address public executor;

    mapping(address => mapping(address => Position)) public positions; // user => token => position
    mapping(address => address[]) private _holders; // token => users who ever opened
    mapping(address => mapping(address => bool)) private _isHolder;

    event PositionOpened(address indexed user, address indexed token, uint256 amount);
    event PositionExited(address indexed user, address indexed token, uint256 amount, uint256 exitPrice);
    event ExecutorSet(address indexed executor);

    error NotOwner();
    error NotExecutor();
    error PositionAlreadyOpen();
    error ZeroAmount();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Set the executor allowed to force-exit positions (owner only).
    function setExecutor(address executor_) external onlyOwner {
        executor = executor_;
        emit ExecutorSet(executor_);
    }

    /// @notice Open (or top up is not supported — one open position per user/token) a mock position.
    function openPosition(address token, uint256 amount) external {
        if (amount == 0) revert ZeroAmount();
        Position storage p = positions[msg.sender][token];
        if (p.open) revert PositionAlreadyOpen();
        p.amount = amount;
        p.open = true;
        if (!_isHolder[token][msg.sender]) {
            _isHolder[token][msg.sender] = true;
            _holders[token].push(msg.sender);
        }
        emit PositionOpened(msg.sender, token, amount);
    }

    /// @notice Close every open position in `token` at `exitPrice`. Executor only.
    /// @return exited Number of positions closed.
    function exitAllForToken(address token, uint256 exitPrice) external returns (uint256 exited) {
        if (msg.sender != executor) revert NotExecutor();
        address[] storage holders = _holders[token];
        for (uint256 i = 0; i < holders.length; i++) {
            Position storage p = positions[holders[i]][token];
            if (p.open) {
                p.open = false;
                exited += 1;
                emit PositionExited(holders[i], token, p.amount, exitPrice);
            }
        }
    }

    /// @notice Number of accounts that have ever opened a position in `token`.
    function holderCount(address token) external view returns (uint256) {
        return _holders[token].length;
    }
}
