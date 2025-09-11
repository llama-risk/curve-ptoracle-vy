from contracts import PtOracle
from moccasin.boa_tools import VyperContract

def deploy() -> VyperContract:
    # Example deployment parameters - these would need to be configured for actual deployment
    pt_address = "0x0000000000000000000000000000000000000001"  # Replace with actual PT token
    underlying_oracle = "0x0000000000000000000000000000000000000003"  # Replace with actual oracle
    slope = 100 * 10**18 // (365 * 24 * 60 * 60)  # Example slope with 1e18 precision
    intercept = 0  # Starting with 0 intercept
    max_update_interval = 86400  # 24 hours
    llamarisk = "0x0000000000000000000000000000000000000004"  # Replace with actual LlamaRisk
    curve_dao = "0x0000000000000000000000000000000000000005"  # Replace with actual Curve DAO
    
    pt_oracle: VyperContract = PtOracle.deploy(
        pt_address,
        underlying_oracle,
        slope,
        intercept,
        max_update_interval,
        llamarisk,
        curve_dao
    )
    
    print(f"PtOracle deployed at: {pt_oracle.address}")
    print(f"Current price: {pt_oracle.price()}")
    
    return pt_oracle

def moccasin_main() -> VyperContract:
    return deploy()
