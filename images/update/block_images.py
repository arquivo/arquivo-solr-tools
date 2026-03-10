
# Usage:
#   This script reads blocking rules from a Google Spreadsheet and checks:
#     1) Solr servers for matching unblocked documents, optionally blocking them.
#     2) The public imagesearch API to ensure no blocked content is visible.
#
#   Example:
#       python block_images.py \
#           --solr "pXX.arquivo.pt:8983,pYY.arquivo.pt:8983" \
#           --api "arquivo.pt/imagesearch" \
#           --pathjson service_account.json \
#           --key <GOOGLE_SHEET_KEY> \
#           --worksheet "Requests"
#
#   Flags:
#       --no-solr         Skip Solr checks entirely.
#       --no-solr-update  Check Solr but do not apply blocking updates. (dry-run)
#       --no-api          Skip imagesearch API validation.
#
#   Requires:
#       - Google service account JSON with access to the spreadsheet
#       - Network access to Solr hosts and the imagesearch API
#       - Python packages: requests, gspread, pandas, gspread_dataframe

import requests
import sys
import json
from datetime import datetime
import gspread
import pandas as pd
from gspread_dataframe import get_as_dataframe, set_with_dataframe
import argparse
import re
import time

# Normalize a timestamp into a 14‑digit YYYYMMDDhhmmss string.
def sanitizeTimestamp(timestamp):
	default="19920101000000"
	timestamp = str(timestamp)
	if(len(timestamp) < 4):
		return default
	if(len(timestamp) < len(default)):
		timestamp=timestamp + default[len(timestamp):]
	return timestamp

# Convert timestamp (YYYYMMDDhhmmss) into a Solr-compatible ISO8601 date string.
def timestampToSolrDate(timestamp):
	timestamp = sanitizeTimestamp(timestamp)
	year = int(timestamp[0:4])
	month = int(timestamp[4:6])
	day = int(timestamp[6:8])
	hour = int(timestamp[8:10])
	minute = int(timestamp[10:12])
	second = int(timestamp[12:14])
	return '"' + datetime(year, month, day, hour, minute, second).isoformat(timespec='milliseconds') + 'Z"'

# Normalize URL for filtering: remove protocol, remove 'www', trim whitespace.
def sanitizeUrl(url):
	reg = re.compile(r"https?://(www\.)?")
	url = reg.sub("", url)
	url = url.strip().replace("(.*)","*")
	reg = re.compile(r"/*$")
	url = reg.sub("", url)
	return url

PAGE_SIZE = 50000      # Number of Solr docs to update per page
API_PAGE_SIZE = 200    # Number of API results per request

# Argument parser for Solr/API/Spreadsheet configuration
parser = argparse.ArgumentParser(description='Script that automatically searches the Solr servers for content that should be blocked and blocks it if needed. It also confirms via the imagesearch API that no such content is publicly available.')
parser.add_argument('-s','--solr', help='Solr hosts to make queries (comma separated if more than one)', default= "HOSTS")
parser.add_argument('-a','--api', help='Imagesearch API endpoint', default= "arquivo.pt/imagesearch")
parser.add_argument('-j','--pathjson', help='Destination of the json file with google service key', default= "JSON")
parser.add_argument('-k','--key', help='Key for Google Spreadsheet with list of blocking requests (the key is in the URL)', default= "SPREADSHEET")
parser.add_argument('-ws','--worksheet', help='Name of the worksheet (tab) of Google Spreadsheet', default= "WORKSHEET")
parser.set_defaults(check_api=True)
parser.add_argument('--no-api', dest='check_api', action='store_false', help='Disable checking the imagesearch API')
parser.set_defaults(check_solr=True)
parser.add_argument('--no-solr', dest='check_solr', action='store_false', help='Disable Solr checking and blocking')
parser.set_defaults(update_solr=True)
parser.add_argument('--no-solr-update', dest='update_solr', action='store_false', help='Disable automatic Solr blocking updates')
args = vars(parser.parse_args())

# Load Google Sheets
gc = gspread.service_account(filename=args['pathjson'])
sh =  gc.open_by_key(args['key'])
worksheet = sh.worksheet(args['worksheet'])
df = get_as_dataframe(worksheet)

# Prepare Solr/API config based on arguments
api_request_host = "http://{0}".format(sanitizeUrl(args['api']))
hosts = list(map(sanitizeUrl,args['solr'].split(',')))

check_api = args['check_api']
check_solr = args['check_solr']
update_solr = args['update_solr']

api_domains = {}
print_output_line = ""

# Column indexes for spreadsheet fields
url_col = 0
timestamp_col = 2
from_col = 3
to_col = 4

if(check_solr):
	print("", flush=True)
	print("-- Checking Solr for blocked results --", flush=True)
	print("", flush=True)

for ind in df.index:

	# Build Solr domain filter for URL variations
	domain = sanitizeUrl(df[df.columns[url_col]][ind])
	domain_base = domain.split('/')[0].split(':')[0]
	domain_extra = domain.split('/',1)[1] if len(domain.split('/')) > 1 else "" 
	domain_filter = "%20OR%20".join([
		"pageHost:{0}*",
		"pageHost:{1}\\:*\\/{2}*",
		"pageHost:www.{0}*",
		"pageHost:www.{1}\\:*\\/{2}*",
		"pageUrl:https\\:\\/\\/{0}*",
		"pageUrl:https\\:\\/\\/{1}\\:*\\/{2}*",
		"pageUrl:http\\:\\/\\/{0}*",
		"pageUrl:http\\:\\/\\/{1}\\:*\\/{2}*",
		"pageUrl:https\\:\\/\\/www.{0}*",
		"pageUrl:https\\:\\/\\/www.{1}\\:*\\/{2}*",
		"imgUrl:https\\:\\/\\/{0}*",
		"imgUrl:https\\:\\/\\/{1}\\:*\\/{2}*",
		"imgUrl:http\\:\\/\\/{0}*",
		"imgUrl:http\\:\\/\\/{1}\\:*\\/{2}*"
		]).format(domain.replace(":","\\:"), domain_base, domain_extra.replace(":","\\:"))

	print_output_line = domain

	query_filter = domain_filter
	api_current_domain = [domain_base,False,False]

	# CASE 1 — Specific timestamp provided
	if(not pd.isnull(df[df.columns[timestamp_col]][ind])):
		timestamp = int(df[df.columns[timestamp_col]][ind])

		# Compute +/- 1 hour range for Solr queries
		timestamp_from = str(timestamp-10000) if (timestamp % 1000000) > 10000 else str(timestamp - (timestamp % 1000000))
		timestamp_to = str(timestamp+10000) if (timestamp % 1000000) < 230000 else str(timestamp - (timestamp % 1000000) + 235959)

		range_from = timestampToSolrDate(timestamp_from)
		range_to = timestampToSolrDate(timestamp_to)
		timestamp_filter = "imgCrawlTimestamp:[{0} TO {1}]%20OR%20pageCrawlTimestamp:[{0} TO {1}]".format(range_from,range_to)

		query_filter = "({0})%20AND%20({1})".format(domain_filter,timestamp_filter)
		api_current_domain = [domain_base,timestamp_from,timestamp_to]

		print_output_line = "{0} at timestamp {1}".format(print_output_line,timestamp)

	# CASE 2 — A timestamp range is provided instead
	elif((not pd.isnull(df[df.columns[from_col]][ind])) or (not pd.isnull(df[df.columns[to_col]][ind]))):
		range_from = "*" if pd.isnull(df[df.columns[from_col]][ind]) else timestampToSolrDate(int(df[df.columns[from_col]][ind]))
		range_to =  "*" if pd.isnull(df[df.columns[to_col]][ind]) else timestampToSolrDate(int(df[df.columns[to_col]][ind]))

		range_filter = "imgCrawlTimestamp:[{0} TO {1}]%20OR%20pageCrawlTimestamp:[{0} TO {1}]".format(range_from,range_to)
		query_filter = "({0})%20AND%20({1})".format(domain_filter,range_filter)

		timestamp_from = sanitizeTimestamp('') if pd.isnull(df[df.columns[from_col]][ind]) else sanitizeTimestamp(int(df[df.columns[from_col]][ind]))
		timestamp_to = datetime.now().strftime("%Y%m%d%H%M%S") if pd.isnull(df[df.columns[to_col]][ind]) else sanitizeTimestamp(int(df[df.columns[to_col]][ind]))
		api_current_domain = [domain_base,timestamp_from,timestamp_to]
		
		print_output_line = "{0} from {1} to {2}".format(
		print_output_line,
		"*" if pd.isnull(df[df.columns[from_col]][ind]) else int(df[df.columns[from_col]][ind]),
		"*" if pd.isnull(df[df.columns[to_col]][ind]) else int(df[df.columns[to_col]][ind])
		)

	# Build Solr query: one with all results, one excluding already-blocked docs
	query_filter_all = query_filter
	query_filter = "({0})%20AND%20-blocked:1".format(query_filter)

	# Group API domain checks by domain/time-range
	if(check_api):
		api_domains.setdefault(json.dumps(api_current_domain),[]).append(domain)

	# Perform Solr queries if enabled
	if(check_solr):
		print("{0}:".format(print_output_line), flush=True)
		for host in hosts:
			description = "    {0}".format(host)

			# Query including all documents
			base_query_all = "http://{0}/solr/images/select?q=".format(host) + query_filter_all
			r = requests.get(base_query_all)
			try:
				counts_all = r.json()["response"]["numFound"]
			except:
				print("Failed to load response for query: {0}".format(base_query_all), flush=True)
				sys.exit()

			# Query only for unblocked documents
			base_query = "http://{0}/solr/images/select?q=".format(host) + query_filter
			r = requests.get(base_query)
			try:
				counts = r.json()["response"]["numFound"]
			except:
				print("Failed to load response for query: {0}".format(base_query), flush=True)
				sys.exit()

			# If unblocked docs exist, block them
			if(counts > 0):
				print("{0}: WARNING - Found {1} images that need to be blocked, out of {2}".format(description,counts,counts_all), flush=True)
				if(update_solr):
					pages = (counts//PAGE_SIZE) + 1
					for i in range(pages):
						r = requests.get("{}&offset={}&fl=id&rows={}".format(base_query, i*PAGE_SIZE, PAGE_SIZE))
						d = [{"id": doc["id"], "blocked": {"set": 1}} for doc in r.json()["response"]["docs"]]
						requests.post("http://{0}/solr/images/update?overwrite=true&commit=true".format(host), json=d)
					print("{0}: Finished blocking {1} images".format(description,counts), flush=True)
			else:
				print("{0}: All {1} images blocked in solr".format(description,counts_all), flush=True)


# Check with imagesearch API if any blocked content is still publicly accessible
if(check_api):

	print("", flush=True)
	print("-- Checking API for blocked results --", flush=True)
	print("", flush=True)

	for key in api_domains:
		params = json.loads(key)
		domain_base = params[0]
		domains = api_domains[key]

		print_output_line = domain_base

		# Build API query parameters
		api_request_query_params = [
		"safeSearch=off",
		"siteSearch={0},www.{0}".format(domain_base),
		"maxItems={0}".format(API_PAGE_SIZE)
		]

		if(params[1] and params[2]):
			api_request_query_params.append("from={0}".format(params[1]))
			api_request_query_params.append("to={0}".format(params[2]))
			print_output_line = "{0} from {1} to {2}".format(print_output_line,params[1],params[2])
		
		print("{0}:".format(print_output_line), flush=True)
		
		api_base_query="{0}?{1}".format(api_request_host,"&".join(api_request_query_params))
		r = requests.get(api_base_query)
		counts = r.json()["totalItems"]

		# API found results → check if they match blocked patterns
		if(counts > 0):
			domains_extra = list(map(lambda domain:domain.split('/',1)[1] if len(domain.split('/')) > 1 else "",domains))

			# If no sub-path constraints exist, simply warn
			if(any(map(lambda domain_extra: len(domain_extra) == 0,domains_extra))):
				print("    WARNING - Found {0} results for the following query: {1}".format(counts,api_base_query), flush=True)
				print("    They should be blocked by one or more of the following rules:", flush=True)
				print("        {0}".format(domains), flush=True)
				print("    Results found:", flush=True)
				idx = 0
				for d in r.json()["responseItems"]:
					print("        {0}:".format(idx), flush=True)
					print("        imgLinkToArchive: {0}".format(d["imgLinkToArchive"]), flush=True)
					print("        pageLinkToArchive: {0}".format(d["pageLinkToArchive"]), flush=True)
					idx += 1
				continue 
			else:
				checked = 0

				# Build regex to identify domain matches in API output
				regex_domain_base = '.*'.join(map(re.escape,domain_base.split('*')))
				regex_domains_extra= list(map(lambda domain_extra: re.escape('/') + '.*'.join(map(re.escape,domain_extra.split('*'))), domains_extra))

				regex_final = '|'.join(list(map(lambda regex_domain_extra: regex_domain_base + regex_domain_extra + '|' + regex_domain_base + r":\d*" + regex_domain_extra,regex_domains_extra)))
				reg = re.compile(regex_final)
				print("    Checking {0} API entries for blocked content using the following regex: {1}".format(counts,regex_final), flush=True)

				currentPage = api_base_query
				blockSuccess = True

				# Iterate through paginated API results
				while (checked < counts and len(r.json()["responseItems"]) > 0):
					bad_apples = list(filter(lambda doc: ( bool(reg.search(doc["pageURL"])) or bool(reg.search(doc["imgSrc"])) ),r.json()["responseItems"]))
					if(len(bad_apples) > 0):
						print("    WARNING - Found blocked results for the following query: {0}".format(currentPage), flush=True)
						blockSuccess = False
						idx = 0
						for d in bad_apples:
							print("        {0}:".format(idx))
							print("        imgLinkToArchive: {0}".format(d["imgLinkToArchive"]), flush=True)
							print("        pageLinkToArchive: {0}".format(d["pageLinkToArchive"]), flush=True)
							idx += 1

					checked = checked + len(r.json()["responseItems"])
					currentPage = r.json()["nextPage"]

					# Avoid hammering the API
					time.sleep(1)
					r = requests.get(currentPage)

				if(blockSuccess):
					print("    Successfully blocked all results from API", flush=True)

		else:
			print("    Successfully blocked all results from API", flush=True)