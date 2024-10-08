# Standard library imports
from datetime import datetime
import json
import os
from threading import Thread
from uuid import uuid4

# Related third-party imports
from flask import Flask, jsonify, request, abort
from flask_cors import CORS
from dotenv import load_dotenv
from gunicorn.app.base import BaseApplication

# Local application/library specific imports
from crew import CompanyResearchCrew
from job_manager import append_event, jobs, jobs_lock, Event
from utils.logging import logger


load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def kickoff_crew(job_id, companies: list[str], positions: list[str]):
	logger.info(f"Crew for job {job_id} is starting")

	results = None
	try:
		company_research_crew = CompanyResearchCrew(job_id)
		company_research_crew.setup_crew(
			companies, positions)
		results = company_research_crew.kickoff()
		logger.info(f"Crew for job {job_id} is complete", results)

	except Exception as e:
		logger.error(f"Error in kickoff_crew for job {job_id}: {e}")
		append_event(job_id, f"An error occurred: {e}")
		with jobs_lock:
			jobs[job_id].status = 'ERROR'
			jobs[job_id].result = str(e)

	with jobs_lock:
		jobs[job_id].status = 'COMPLETE'
		jobs[job_id].result = results
		jobs[job_id].events.append(
			Event(timestamp=datetime.now(), data="Crew complete"))


@app.route('/api/crew', methods=['POST'])
def run_crew():
	logger.info("Received request to run crew")
	# Validation
	data = request.json
	if not data or 'companies' not in data or 'positions' not in data:
		abort(400, description="Invalid input data provided.")

	job_id = str(uuid4())
	companies = data['companies']
	positions = data['positions']

	thread = Thread(target=kickoff_crew, args=(
		job_id, companies, positions))
	thread.start()

	return jsonify({"job_id": job_id}), 202


@app.route('/api/crew/<job_id>', methods=['GET'])
def get_status(job_id):
	with jobs_lock:
		job = jobs.get(job_id)
		if job is None:
			abort(404, description="Job not found")

	 # Parse the job.result string into a JSON object
	try:
		result_json = json.loads(job.result)
	except json.JSONDecodeError:
		# If parsing fails, set result_json to the original job.result string
		result_json = job.result

	return jsonify({
		"job_id": job_id,
		"status": job.status,
		"result": result_json,
		"events": [{"timestamp": event.timestamp.isoformat(), "data": event.data} for event in job.events]
	})


class StandaloneApplication(BaseApplication):
	def __init__(self, app, options=None):
		self.options = options or {}
		self.application = app
		super().__init__()

	def load_config(self):
		config = {key: value for key, value in self.options.items()
				  if key in self.cfg.settings and value is not None}
		for key, value in config.items():
			self.cfg.set(key.lower(), value)

	def load(self):
		return self.application


if __name__ == '__main__':
	is_production = os.environ.get('ENVIRONMENT', 'development').lower() == 'production'
	
	if is_production:
		options = {
			'bind': f"0.0.0.0:{int(os.environ.get('PORT', 3001))}",
			'workers': 4,  # Adjust based on your needs
			'worker_class': 'gevent',
			'timeout': 120,
		}
		StandaloneApplication(app, options).run()
	else:
		# Run in development mode
		app.run(host='localhost', port=int(os.environ.get('PORT', 3001)), debug=True)