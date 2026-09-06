from __future__ import annotations
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        from razorbeam_terraria.worker import main
        raise SystemExit(main(sys.argv[2]))
    from razorbeam_terraria.main import main
    raise SystemExit(main())
