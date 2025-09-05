# pragma version 0.4.3
# @license MIT

# Interface for Principal Token example pt token: 0x6d98a2b6cdbf44939362a3e99793339ba2016af4
interface PT:
    def expiry() -> uint256: view

# Interface for underlying oracle
interface Oracle:
    def price() -> uint256: view

# Constants
DISCOUNT_PRECISION: constant(uint256) = 10**8

# State variables
pt: public(address)
underlying_oracle: public(address)
slope: public(uint256)  # Linear discount slope with 1e8 precision
intercept: public(uint256)  # Linear discount intercept with 1e8 precision
max_update_interval: public(uint256)
pt_expiry: public(uint256)

# Access control
llamarisk: public(address)
curve_dao: public(address)

# Modifiers
@internal
def _only_llamarisk():
    assert msg.sender == self.llamarisk, "dev: caller is not LlamaRisk"

@internal
def _only_curve_dao():
    assert msg.sender == self.curve_dao, "dev: caller is not Curve DAO"

# Price storage
last_update: public(uint256)
current_price: public(uint256)

# Rate limiting for set_linear_discount
last_discount_update: public(uint256)

# Events
event LinearDiscountUpdated:
    slope: uint256
    intercept: uint256

event LimitsUpdated:
    max_update_interval: uint256

# Constructor
@deploy
def __init__(
    _pt: address,
    _underlying_oracle: address,
    _slope: uint256,
    _intercept: uint256,
    _max_update_interval: uint256,
    _llamarisk: address,
    _curve_dao: address
):
    self.pt = _pt
    self.underlying_oracle = _underlying_oracle
    self.slope = _slope
    self.intercept = _intercept
    self.max_update_interval = _max_update_interval
    self.llamarisk = _llamarisk
    self.curve_dao = _curve_dao
    self.last_update = block.timestamp
    self.last_discount_update = block.timestamp  # Initialize discount update timestamp
    self.pt_expiry = staticcall PT(self.pt).expiry()

    # Initialize price
    self.current_price = self._calculate_price()

# Internal functions
@internal
@view
def _calculate_price() -> uint256:

    # Get underlying oracle price and apply discount
    underlying_price: uint256 = staticcall Oracle(self.underlying_oracle).price()

    if self.pt_expiry <= block.timestamp:
        return (underlying_price * 1 * DISCOUNT_PRECISION) // DISCOUNT_PRECISION


    # Calculate discount based on time to maturity
    time_to_maturity: uint256 = self.pt_expiry - block.timestamp

    # Linear discount with 1e8 precision: discount = slope * time_in_seconds + intercept
    discount: uint256 = self.slope * time_to_maturity + self.intercept
    
    # TODO: We have to decide what to do if discount >= DISCOUNT_PRECISION (never should be the case but just in case)
    if discount >= DISCOUNT_PRECISION:
        discount = 1 * DISCOUNT_PRECISION  # Full discount
    
    # Calculate discount factor (between 0 and 1 with 1e8 precision)
    discount_factor: uint256 = DISCOUNT_PRECISION - discount
    
    # Return discounted price: underlying_price * discount_factor / DISCOUNT_PRECISION
    return (underlying_price * discount_factor) // DISCOUNT_PRECISION

# View functions
@view
@external
def price() -> uint256:
    return self._calculate_price()

# Write functions - All callers
@external
@nonpayable
def price_w() -> uint256:
    # If timestamp is the same, just return current price (no update needed)
    if block.timestamp == self.last_update:
        return self.current_price
    
    # Calculate new price using linear discount model
    new_price: uint256 = self._calculate_price()
    
    # Update price and timestamp
    self.current_price = new_price
    self.last_update = block.timestamp
    
    return new_price

# Internal function to update discount parameters
@internal
def _update_discount_params(_slope: uint256, _intercept: uint256):
    # Update parameters
    self.slope = _slope
    self.intercept = _intercept
    self.last_discount_update = block.timestamp
    
    log LinearDiscountUpdated(slope=_slope, intercept=_intercept)

# Write functions - LlamaRisk only
@external
@nonpayable
def set_linear_discount(_slope: uint256, _intercept: uint256):
    self._only_llamarisk()
    
    # Check rate limiting - LlamaRisk can only update once per max_update_interval
    assert block.timestamp >= self.last_discount_update + self.max_update_interval, "dev: update interval not elapsed"
    
    self._update_discount_params(_slope, _intercept)

@external
@nonpayable
def set_slope_from_apy(expected_apy: uint256):
    """
    @notice Set slope based on expected APY, with intercept set to 0
    @param expected_apy Expected APY with 1e8 precision
    """
    self._only_llamarisk()
    
    # Check rate limiting - LlamaRisk can only update once per max_update_interval
    assert block.timestamp >= self.last_discount_update + self.max_update_interval, "dev: update interval not elapsed"
    
    # Constants
    SECONDS_PER_YEAR: uint256 = 365 * 24 * 60 * 60  # ~31,536,000 seconds
    
    # Calculate numerator and denominator to avoid precision loss
    numerator: uint256 = expected_apy
    denominator: uint256 = (DISCOUNT_PRECISION + expected_apy) * SECONDS_PER_YEAR
    
    # Calculate slope with proper precision
    new_slope: uint256 = (numerator * DISCOUNT_PRECISION) // denominator
    
    self._update_discount_params(new_slope, 0)

# Write functions - Curve DAO only
@external
@nonpayable
def set_limits(_max_update_interval: uint256):
    self._only_curve_dao()
    assert _max_update_interval > 0, "dev: invalid update interval"
    
    self.max_update_interval = _max_update_interval
    
    log LimitsUpdated(max_update_interval=_max_update_interval)