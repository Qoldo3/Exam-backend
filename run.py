import os

import uvicorn

if __name__ == "__main__":
    # reload is opt-in (RELOAD=1) because on Windows the reloader child keeps
    # holding the port when the parent is killed, leaving orphaned listeners
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=os.environ.get("RELOAD") == "1",
    )
