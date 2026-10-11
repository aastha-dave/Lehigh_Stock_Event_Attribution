from app import create_app
from app.services.pipeline_service import run_batch_episode_pipeline

app = create_app()

with app.app_context():
    result = run_batch_episode_pipeline(limit=1, force_recompute=True)
    print(f"\nTotal: {result['total']}  Success: {result['success']}  Failed: {result['failed']}")
    for r in result["results"]:
        print(r)