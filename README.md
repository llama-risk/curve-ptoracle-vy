# PtOracle: A Deterministic Price Oracle for Pendle PTs

This repository contains the `PtOracle`, a Vyper smart contract that provides a deterministic price feed for Pendle's Principal Tokens (PTs). It is designed for integration with lending protocols like LlamaLend, providing a stable pricing mechanism for PTs used as collateral.

The project is built using the [Moccasin](https://cyfrin.github.io/moccasin) framework for Vyper development and testing.

## Problem

Principal Tokens (PTs) often exhibit high price volatility on Automated Market Makers (AMMs), making them risky to use as collateral in lending markets. The `PtOracle` addresses this by implementing a deterministic, discount-based pricing model that provides the stability required for safe and reliable lending operations.

## Key Features

*   **Linear Discount Model**: Calculates the PT price based on its time to maturity using a configurable linear formula: `price = underlying_price * (1 - (slope * time_to_maturity + intercept))`.
*   **Dual Price Functions**:
    *   `price() -> uint256`: A view function that returns the current calculated price of the PT.
    *   `price_w() -> uint256`: A state-modifying function that calculates, caches, and returns the price.
*   **Role-Based Access Control**: Administrative functions are restricted to specific roles:
    *   **LlamaRisk**: Can update the discount parameters (`slope`, `intercept`).
    *   **Curve DAO**: Can set rate-limiting parameters.
*   **Rate Limiting**: A configurable delay (`max_update_interval`) is enforced between updates to the discount parameters to prevent price manipulation.
*   **Expired PT Handling**: The oracle returns a price of `1` for PTs that have passed their expiry date.

## Project Structure

The repository is organized as follows:

```
├── contracts/
│   └── PtOracle.vy      # The core PtOracle smart contract
├── tests/
│   └── test_pt_oracle.py # Tests for the oracle
├── script/
│   └── deploy.py        # Deployment script
├── guidelines/
│   └── *.md             # Design and specification documents
└── moccasin.toml        # Moccasin project configuration
```

## Getting Started

To set up the project for local development, install the dependencies using `uv`:

```bash
uv pip install -e .
```
This command will install the project in editable mode and all its dependencies defined in `pyproject.toml`.

## Usage

This project uses the Moccasin framework for development. Here are the primary commands:

*   **Compile the contract:**
    ```bash
    mox compile
    ```

*   **Run tests:**
    ```bash
    mox test
    ```

*   **Deploy the contract:**
    The deployment script in `script/deploy.py` must be configured first.
    ```bash
    mox run deploy
    ```

## Contribution Instructions

Contributions are welcome! If you have suggestions or find a bug, please open an issue or submit a pull request.


1.  Create a new branch for your feature or bug fix.
2.  Make your changes and ensure all tests pass.
3.  Submit a pull request with a clear description of your changes.