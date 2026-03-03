#!/usr/bin/env python3
"""Quick balance check for earthquake bot wallet."""

import os
import sys
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# Список RPC для fallback
RPCS = [
    'https://polygon-bor-rpc.publicnode.com',
    'https://rpc.ankr.com/polygon',
    'https://polygon.drpc.org',
    os.getenv('POLYGON_RPC'),  # Из .env
]

PK = os.getenv('PK')
if not PK:
    print("❌ Ошибка: PK не найден в .env")
    sys.exit(1)

def check_balance(rpc_url, timeout=10):
    """Проверка баланса через конкретный RPC."""
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': timeout}))

        # Проверяем подключение
        if not w3.is_connected():
            return None, "Не удалось подключиться"

        account = w3.eth.account.from_key(PK)

        # Баланс MATIC/POL
        matic_wei = w3.eth.get_balance(account.address)
        matic = matic_wei / 1e18

        # Баланс USDC.e
        USDC_E = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
        usdc_abi = [{'name': 'balanceOf', 'type': 'function',
                     'inputs': [{'name': 'account', 'type': 'address'}],
                     'outputs': [{'name': '', 'type': 'uint256'}]}]

        usdc_contract = w3.eth.contract(address=USDC_E, abi=usdc_abi)
        usdc_wei = usdc_contract.functions.balanceOf(account.address).call()
        usdc = usdc_wei / 1e6

        return {
            'address': account.address,
            'matic': matic,
            'usdc_e': usdc,
            'rpc': rpc_url
        }, None

    except Exception as e:
        return None, str(e)

def main():
    print("🔍 Проверка баланса бота землетрясений...")
    print()

    # Пробуем разные RPC
    for rpc in RPCS:
        if not rpc:
            continue

        print(f"Пробую: {rpc}")
        result, error = check_balance(rpc)

        if result:
            print(f"✅ Успех!")
            print()
            print(f"Address: {result['address']}")
            print(f"MATIC/POL: {result['matic']:.4f}")
            print(f"USDC.e: ${result['usdc_e']:.2f}")
            print()

            if result['matic'] < 0.1:
                print("⚠️ МАЛО ГАЗА! Пополните MATIC")
            elif result['matic'] < 0.5:
                print("🟡 Газа мало, рекомендуется пополнить")
            else:
                print("✅ Газа достаточно")

            return 0
        else:
            print(f"❌ Ошибка: {error}")
            print()

    print("❌ Все RPC недоступны!")
    print()
    print("Проверьте вручную:")
    print("https://polygonscan.com/address/0xff36fc6De4CCDd290C14EE69244c21c1803Ad5b7")
    return 1

if __name__ == '__main__':
    sys.exit(main())
