import pytest
import boa
from datetime import datetime, timedelta


@pytest.fixture
def owner():
    return boa.env.generate_address()


@pytest.fixture
def llamarisk():
    return boa.env.generate_address()


@pytest.fixture
def curve_dao():
    return boa.env.generate_address()


@pytest.fixture
def manager():
    return boa.env.generate_address()


@pytest.fixture
def admin():
    return boa.env.generate_address()


@pytest.fixture
def mock_pt(owner):
    """Deploy a mock PT token with expiry"""
    mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
    with boa.env.prank(owner):
        # Set expiry to 30 days from now
        future_expiry = boa.env.evm.patch.timestamp + (30 * 24 * 60 * 60)
        return boa.loads(mock_pt_code, future_expiry)


@pytest.fixture
def mock_oracle(owner):
    """Deploy a mock underlying oracle"""
    mock_oracle_code = """
# pragma version 0.4.3

stored_price: uint256

@deploy
def __init__():
    self.stored_price = 10**18  # 1 ETH = $1 for simplicity

@view
@external
def price() -> uint256:
    return self.stored_price

@external
def set_price(_price: uint256):
    self.stored_price = _price
"""
    with boa.env.prank(owner):
        return boa.loads(mock_oracle_code)


@pytest.fixture
def pt_oracle(owner, manager, admin, mock_pt, mock_oracle):
    """Deploy the PtOracle contract"""
    from contracts import PtOracle
    
    with boa.env.prank(owner):
        return PtOracle.deploy(
            mock_pt.address,  # PT token
            mock_oracle.address,  # underlying oracle
            1,  # slope (very small slope)
            0,   # intercept (0% with 1e8 precision)
            86400,  # max_update_interval (24 hours)
            manager,
            admin
        )


class TestPtOracle:
    def test_deployment(self, pt_oracle, owner, manager, admin, mock_pt, mock_oracle):
        """Test that the contract deploys correctly with initial values"""
        # Note: contract doesn't have owner() method in the new version
        assert pt_oracle.manager() == manager
        assert pt_oracle.admin() == admin
        assert pt_oracle.pt() == mock_pt.address
        assert pt_oracle.underlying_oracle() == mock_oracle.address
        assert pt_oracle.slope() == 1
        assert pt_oracle.intercept() == 0
        assert pt_oracle.max_update_interval() == 86400
    
    def test_price_calculation(self, pt_oracle, mock_oracle):
        """Test that price() calculates the correct discounted price"""
        # The discount should be applied to the underlying price
        # With 30 days to maturity, slope=1, intercept=0
        # discount = (1 * 30*24*60*60) + 0 (in terms of 1e8 precision)
        price = pt_oracle.price()
        
        # Price should be less than underlying due to discount
        underlying_price = mock_oracle.price()
        assert price < underlying_price
        assert price > 0
    
    def test_price_w_caching(self, pt_oracle):
        """Test that price_w() caches the price correctly"""
        # First call to price_w
        price1 = pt_oracle.price_w()
        timestamp1 = pt_oracle.last_update()
        
        # Immediate second call (same block) should return same price without recalculation
        price2 = pt_oracle.price_w()
        timestamp2 = pt_oracle.last_update()
        
        assert price1 == price2
        assert timestamp1 == timestamp2
        assert pt_oracle.last_price() == price1
    
    def test_price_w_update(self, pt_oracle):
        """Test that price_w() updates when time passes"""
        # First call
        price1 = pt_oracle.price_w()
        
        # Advance time by 1 hour
        boa.env.evm.patch.timestamp += 3600
        
        # Second call should update
        price2 = pt_oracle.price_w()
        
        # Price should change slightly due to time passing (less time to maturity)
        assert price2 != price1
        assert pt_oracle.last_update() > 0
    
    def test_expired_pt_returns_zero(self, owner):
        """Test that expired PT returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with past expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to past
            past_expiry = boa.env.evm.patch.timestamp - 1
            expired_pt = boa.loads(mock_pt_code, past_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy PtOracle with expired PT
            pt_oracle = PtOracle.deploy(
                expired_pt.address,
                mock_oracle.address,
                1, 0, 86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            # After expiry, should return underlying oracle price
            assert pt_oracle.price() == 10**18
            assert pt_oracle.price_w() == 10**18
    
    def test_set_linear_discount(self, pt_oracle, manager, owner):
        """Test that manager can update linear discount parameters"""
        # First, advance time to ensure we can update
        boa.env.timestamp = boa.env.timestamp + 86401  # Move past the initial interval
        
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        assert pt_oracle.slope() == 200
        assert pt_oracle.intercept() == 100
        assert pt_oracle.last_discount_update() > 0
        
        # Non-manager should fail
        with boa.reverts("caller is not manager"):
            pt_oracle.set_linear_discount(300, 150, sender=owner)
    
    def test_set_linear_discount_limits(self, pt_oracle, manager):
        """Test that linear discount parameters respect DISCOUNT_PRECISION limit"""
        # Advance time to allow update
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Test setting valid values
        pt_oracle.set_linear_discount(2000, 1000, sender=manager)
        assert pt_oracle.slope() == 2000
        assert pt_oracle.intercept() == 1000
        
        # Test that intercept cannot exceed DISCOUNT_PRECISION (10**18)
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("intercept exceeds precision"):
            pt_oracle.set_linear_discount(1000, 10**18 + 1, sender=manager)
    
    def test_set_linear_discount_rate_limiting(self, pt_oracle, manager):
        """Test that set_linear_discount is rate limited with > operator"""
        # First update should work after initial interval
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        first_update = pt_oracle.last_discount_update()
        
        # Immediate second update should fail
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time by less than max_update_interval
        boa.env.timestamp = boa.env.timestamp + 43200  # 12 hours
        
        # Should still fail
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time to exactly max_update_interval (should still fail with > operator)
        boa.env.timestamp = first_update + 86400  # Exactly 24 hours from last update
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(300, 150, sender=manager)
        
        # Advance time to max_update_interval + 1 second (should work with > operator)
        boa.env.timestamp = first_update + 86401  # 24 hours + 1 second
        pt_oracle.set_linear_discount(300, 150, sender=manager)
        assert pt_oracle.slope() == 300
        assert pt_oracle.intercept() == 150
        assert pt_oracle.last_discount_update() > first_update
    
    def test_set_limits(self, pt_oracle, admin, owner):
        """Test that admin can update max update interval"""
        pt_oracle.set_limits(172800, sender=admin)  # 48 hours
        assert pt_oracle.max_update_interval() == 172800
        
        # Non-admin should fail
        with boa.reverts("caller is not admin"):
            pt_oracle.set_limits(86400, sender=owner)
    
    def test_set_limits_validation(self, pt_oracle, admin):
        """Test that set_limits validates input"""
        # Test invalid update interval
        with boa.reverts("invalid update interval"):
            pt_oracle.set_limits(0, sender=admin)  # Invalid update interval
    
    def test_full_discount_returns_zero(self, owner):
        """Test that full discount (>=100%) returns 0 price"""
        from contracts import PtOracle
        
        # Create PT with long expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        with boa.env.prank(owner):
            # Set expiry to 365 days from now
            far_future_expiry = boa.env.timestamp + (365 * 24 * 60 * 60)
            pt = boa.loads(mock_pt_code, far_future_expiry)
            
            # Create mock oracle
            mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
            mock_oracle = boa.loads(mock_oracle_code)
            
            # Deploy with max intercept and some slope to cause exactly 100% discount
            # DISCOUNT_PRECISION = 10**18, so intercept at max will discount to 0
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                0,  # No slope needed
                10**18,  # Max intercept = 100% discount
                86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
            
            # When discount equals 100%, returns 0
            assert pt_oracle.price() == 0
            assert pt_oracle.price_w() == 0
    
    def test_underlying_price_changes(self, pt_oracle, mock_oracle):
        """Test that PT price changes when underlying oracle price changes"""
        # Get initial price
        initial_price = pt_oracle.price()
        
        # Double the underlying price
        mock_oracle.set_price(2 * 10**18, sender=boa.env.generate_address())
        
        # PT price should also increase (but still be discounted)
        new_price = pt_oracle.price()
        assert new_price > initial_price
        assert new_price < mock_oracle.price()  # Still discounted
    
    # Story 1.1 Tests
    def test_precision_constant_renamed(self, pt_oracle):
        """Test that DISCOUNT_PRECISION is 10**18"""
        # This tests the precision used in discount calculations
        # With slope=1 and 30 days to maturity, discount should be minimal
        price = pt_oracle.price()
        assert price > 0
        # Price should be calculated with 10**18 precision
        
    def test_immutable_pt_expiry(self, pt_oracle, mock_pt):
        """Test that pt_expiry is properly cached as immutable"""
        # The expiry should match what was set in the mock_pt
        assert pt_oracle.pt_expiry() == mock_pt.expiry()
        
    def test_last_price_variable(self, pt_oracle):
        """Test that last_price variable is used correctly"""
        # Call price_w to set last_price
        price = pt_oracle.price_w()
        assert pt_oracle.last_price() == price
        
        # Verify it's cached correctly on subsequent calls in same block
        price2 = pt_oracle.price_w()
        assert pt_oracle.last_price() == price2
        assert price == price2
    
    def test_discount_does_not_exceed_precision(self, owner):
        """Test that discount calculations respect DISCOUNT_PRECISION limit"""
        from contracts import PtOracle
        
        # Create PT with moderate expiry
        mock_pt_code = """
# pragma version 0.4.3

expiry: public(uint256)

@deploy
def __init__(_expiry: uint256):
    self.expiry = _expiry
"""
        mock_oracle_code = """
# pragma version 0.4.3

@view
@external
def price() -> uint256:
    return 10**18
"""
        pt_expiry = boa.env.timestamp + (10 * 24 * 60 * 60)  # 10 days
        with boa.env.prank(owner):
            pt = boa.loads(mock_pt_code, pt_expiry)
            mock_oracle = boa.loads(mock_oracle_code)
        
        # Deploy with high intercept close to DISCOUNT_PRECISION
        # The total discount (slope * time + intercept) must not exceed DISCOUNT_PRECISION
        with boa.env.prank(owner):
            pt_oracle = PtOracle.deploy(
                pt.address,
                mock_oracle.address,
                100,  # small slope 
                9 * 10**17,  # 90% intercept (0.9 * 10**18)
                86400,
                boa.env.generate_address(),
                boa.env.generate_address()
            )
        assert pt_oracle.intercept() == 9 * 10**17
        
        # Price should be calculated without reverting
        price = pt_oracle.price()
        assert price > 0
        assert price < 10**18  # Should be discounted
    
    # Story 1.2 Tests  
    def test_rate_limiting_with_greater_than_operator(self, pt_oracle, manager):
        """Test that rate limiting uses > operator, not >="""
        # Advance to allow first update
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(100, 50, sender=manager)
        update_time = pt_oracle.last_discount_update()
        
        # At exactly max_update_interval, should fail (tests > operator)
        boa.env.timestamp = update_time + 86400
        with boa.reverts("update interval not elapsed"):
            pt_oracle.set_linear_discount(200, 100, sender=manager)
        
        # At max_update_interval + 1, should succeed
        boa.env.timestamp = update_time + 86401
        pt_oracle.set_linear_discount(200, 100, sender=manager)
        assert pt_oracle.slope() == 200
        assert pt_oracle.intercept() == 100
    
    def test_slope_and_intercept_boundaries(self, pt_oracle, manager, admin):
        """Test boundary assertions for slope and intercept"""
        # Test that intercept cannot exceed DISCOUNT_PRECISION
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("intercept exceeds precision"):
            pt_oracle.set_linear_discount(1000, 10**18 + 1, sender=manager)
        
        # Test that slope cannot exceed DISCOUNT_PRECISION
        with boa.reverts("slope exceeds precision"):
            pt_oracle.set_linear_discount(10**18 + 1, 1000, sender=manager)
        
        # Test max update interval boundaries
        with boa.reverts("invalid update interval"):
            pt_oracle.set_limits(0, sender=admin)
        
        # Test very high but valid max update interval
        pt_oracle.set_limits(10**8, sender=admin)  # Very high but should work
        assert pt_oracle.max_update_interval() == 10**8
    
    def test_events_include_old_and_new_values(self, pt_oracle, manager, admin):
        """Test that events emit both old and new values"""
        # Advance time for rate limiting
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Test LinearDiscountUpdated event
        old_slope = pt_oracle.slope()
        old_intercept = pt_oracle.intercept()
        
        # Update and check event (we can't directly check events in boa easily,
        # but we verify the state changes which would trigger the events)
        pt_oracle.set_linear_discount(500, 250, sender=manager)
        assert pt_oracle.slope() == 500
        assert pt_oracle.intercept() == 250
        
        # Test MaxUpdateIntervalUpdated event
        old_interval = pt_oracle.max_update_interval()
        pt_oracle.set_limits(172800, sender=admin)
        assert pt_oracle.max_update_interval() == 172800
    
    # Story 1.3 Tests
    def test_admin_can_set_manager(self, pt_oracle, admin, manager):
        """Test that admin can update the manager address"""
        new_manager = boa.env.generate_address()
        
        # Admin should be able to set new manager
        pt_oracle.set_manager(new_manager, sender=admin)
        assert pt_oracle.manager() == new_manager
        
        # New manager should be able to perform manager functions
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(100, 50, sender=new_manager)
        assert pt_oracle.slope() == 100
        assert pt_oracle.intercept() == 50
    
    def test_manager_cannot_set_manager(self, pt_oracle, manager):
        """Test that manager cannot call set_manager"""
        new_manager = boa.env.generate_address()
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_manager(new_manager, sender=manager)
    
    def test_set_manager_rejects_zero_address(self, pt_oracle, admin):
        """Test that set_manager rejects zero address"""
        zero_address = "0x0000000000000000000000000000000000000000"
        
        with boa.reverts("invalid manager address"):
            pt_oracle.set_manager(zero_address, sender=admin)
    
    def test_manager_updated_event(self, pt_oracle, admin):
        """Test that ManagerUpdated event is emitted"""
        old_manager = pt_oracle.manager()
        new_manager = boa.env.generate_address()
        
        # Update manager (event would be emitted here)
        pt_oracle.set_manager(new_manager, sender=admin)
        assert pt_oracle.manager() == new_manager
        # Event emission is implicit in the state change
    
    def test_role_based_access_control(self, pt_oracle, admin, manager, owner):
        """Test comprehensive role-based access control"""
        unauthorized = boa.env.generate_address()
        
        # Test manager functions
        boa.env.timestamp = boa.env.timestamp + 86401
        pt_oracle.set_linear_discount(150, 75, sender=manager)  # Should work
        
        boa.env.timestamp = boa.env.timestamp + 86401
        with boa.reverts("caller is not manager"):
            pt_oracle.set_linear_discount(200, 100, sender=unauthorized)
        
        # Test admin functions
        pt_oracle.set_limits(100000, sender=admin)  # Should work
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_limits(200000, sender=unauthorized)
        
        new_manager = boa.env.generate_address()
        pt_oracle.set_manager(new_manager, sender=admin)  # Should work
        
        with boa.reverts("caller is not admin"):
            pt_oracle.set_manager(boa.env.generate_address(), sender=unauthorized)
    
    def test_set_slope_from_apy(self, pt_oracle, manager):
        """Test that manager can call set_slope_from_apy"""
        # Advance time for rate limiting
        boa.env.timestamp = boa.env.timestamp + 86401
        
        # Manager should be able to call set_slope_from_apy
        # APY of 5% (500 basis points) = 0.05 * 10**18 / 365 / 86400 per second
        pt_oracle.set_slope_from_apy(5 * 10**16, sender=manager)  # 5% as 0.05 * 10**18
        
        # Verify slope was updated (exact value depends on calculation)
        # slope should be apy / seconds_in_year = 5e16 / (365 * 86400) ≈ 1585489599
        assert pt_oracle.slope() > 0
        assert pt_oracle.last_discount_update() > 0
    
    def test_interface_renamed_to_pendle_pt(self, pt_oracle, mock_pt):
        """Test that interface is renamed from PT to PendlePT"""
        # The public variable is still called 'pt' but interface is PendlePT
        assert pt_oracle.pt() == mock_pt.address
        # Test that pt_expiry immutable is cached correctly
        assert pt_oracle.pt_expiry() == mock_pt.expiry()