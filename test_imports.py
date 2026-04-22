try:
    from src.orchestrator import ChatOrchestrator
    print("imports OK")
except Exception as e:
    print(f"IMPORT ERROR: {e}")
    import traceback
    traceback.print_exc()
