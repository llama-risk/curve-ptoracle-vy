# PtOracle Security Audit Documentation

## Contract Overview

**Contract Name:** PtOracle.vy
**Version:** Vyper 0.4.3
**License:** BUSL-1.1
**Purpose:** Oracle for pricing Pendle Principal Tokens (PT) by applying time-based linear discount to underlying asset prices

## Architecture Summary

The PtOracle contract implements a pricing oracle for Pendle Principal Tokens that:
1. Fetches underlying asset prices from an external oracle
2. Applies time-based linear discount based on time to maturity
3. Implements role-based access control for parameter updates
4. Provides both view and state-changing price retrieval methods

### Core Formula
```
price = underlying_price * (1 - discount)
discount = slope * time_to_maturity_years + intercept
```

### Role-Based Access Control

Three-tier permission system:
1. **ADMIN_ROLE:** Can manage other roles (grant/revoke)
2. **MANAGER_ROLE:** Can update discount parameters (slope, intercept)
3. **PARAMETER_ADMIN_ROLE:** Can set rate limits and change boundaries

**Security Note:** Deployer automatically renounces DEFAULT_ADMIN_ROLE after setup

## Security Assumptions & Invariants

### Core Invariants
1. **Discount Bound:** `discount ≤ 100%` (enforced by parameter validation)
2. **Price Monotonicity:** As time approaches expiry, price converges to underlying
3. **Post-Expiry Convergence:** After expiry, PT price = underlying price exactly
4. **Time Consistency:** `block.timestamp ≤ pt_expiry` determines discount application

### Trust Assumptions
1. **Block Timestamp Reliability:** Contract assumes `block.timestamp` is accurate
2. **External Oracle Integrity:** Underlying oracle provides manipulation-resistant prices
3. **PT Contract Immutability:** PT expiry is immutable and correctly implemented
4. **Arithmetic Safety:** Vyper 0.4.3 built-in overflow protection is sufficient

### Economic Assumptions
1. **Linear Discount Model:** Assumes linear relationship between time and discount is appropriate
2. **Parameter Stability:** Frequent parameter changes are prevented via rate limiting
3. **Market Efficiency:** Price updates reflect fair market value when called

## Areas Requiring Special Audit Focus

1. **Precision Loss:** Division operations in price calculations (Lines 166-179) and behavior near PT expiry
2. **Access Control:** Role hierarchy setup and DEFAULT_ADMIN_ROLE revocation in constructor (Lines 105-117)
3. **MEV Risks:** Parameter update functions susceptible to sandwich attacks (Lines 262-270, 274-305)
4. **Time Dependencies:** Edge cases at PT expiry and timestamp manipulation risks (Lines 160-163, 194-195)
5. **Rate Limiting:** Zero-value implications and bypass scenarios (Lines 266-268, 283-285)
6. **Arithmetic Operations:** Subtraction safety in parameter change validations (Lines 221-234)
7. **External Calls:** Trust assumptions for PT and oracle contracts via staticcall (Lines 134, 158)

## Known Limitations & Accepted Risks

1. **Oracle Dependency:** Price accuracy entirely depends on underlying oracle integrity
2. **Linear Model Limitation:** May not accurately reflect complex market dynamics
3. **Timestamp Dependency:** Miners have limited manipulation capability (typically ±15 seconds)
4. **No Emergency Pause:** Contract lacks circuit breaker for critical situations
5. **Immutable PT Expiry:** Cannot adjust for PT contract upgrades or migrations