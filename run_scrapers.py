#!script

# ./run_scrapers.py text bills votes stats

import os, os.path, glob, re, hashlib, shutil, sys, datetime

CONGRESS = int(os.environ.get("CONGRESS", "113"))
SCRAPER_PATH = "../scripts/congress"

# UTILS

bill_type_map = { 'hr': 'h', 's': 's', 'hres': 'hr', 'sres': 'sr', 'hjres': 'hj', 'sjres': 'sj', 'hconres': 'hc', 'sconres': 'sc' }

def mkdir(path):
	if not os.path.exists(path):
		os.makedirs(path)

def md5(fn, modulo=None):
	# do an MD5 on the file but run a regex first
	# to remove content we don't want to check for
	# differences.
	
	with open(fn) as fobj:
		data = fobj.read()
	if modulo != None: data = re.sub(modulo, "--", data)
	
	md5 = hashlib.md5()
	md5.update(data)
	return md5.digest()

def copy(fn1, fn2, modulo):
	# Don't copy true unchanged files because we want to keep
	# file contents the same so long as no real data changed.
	# When we load into our db, we use hashes to check if we
	# need to process a file. And for rsync users, don't make
	# them re-download files that have no real changes.
	if os.path.exists(fn2):
		if md5(fn1, modulo) == md5(fn2, modulo):
			return False
	#print fn2
	shutil.copy2(fn1, fn2)
	return True

def make_link(src, dest):
	if not os.path.exists(dest):
		os.link(src, dest)
	elif os.stat(src).st_ino == os.stat(dest).st_ino:
		pass # files are the same (hardlinked)
	else:
		if md5(src) != md5(dest):
			print "replacing", src, dest
		else:
			print "squashing existing file", src, dest
		os.unlink(dest)
		os.link(src, dest)

# MAIN

# Set options.

fetch_mode = "--force --fast"
log_level = "error"

if "full-scan" in sys.argv: fetch_mode = "--force"
if "CACHE" in os.environ: fetch_mode = "--fast"
if "DEBUG" in os.environ: log_level = "info"
	
# Run scrapers and parsers.

if "people" in sys.argv:
	if CONGRESS != 113: raise ValueErrror()
	
	# Pull latest poeple YAML.
	os.system("cd %s/congress-legislators; git fetch -pq" % SCRAPER_PATH)
	os.system("cd %s/congress-legislators; git merge --ff-only -q origin/master" % SCRAPER_PATH)
	
	# Convert people YAML into the legacy format and alternative formats.
	mkdir("data/us/%d" % CONGRESS)
	os.system("python ../scripts/legacy-conversion/convert_people.py %s/congress-legislators/ data/us/people_legacy.xml data/us/people.xml 0" % SCRAPER_PATH)
	os.system("python ../scripts/legacy-conversion/convert_people.py %s/congress-legislators/ data/us/people_legacy.xml data/us/%d/people.xml 1" % (SCRAPER_PATH, CONGRESS))
	os.system("cd %s/congress-legislators/scripts; . .env/bin/activate; python alternate_bulk_formats.py" % SCRAPER_PATH)

	# Copy into our public directory.
	for f in glob.glob("%s/congress-legislators/*.yaml" % SCRAPER_PATH):
		make_link(f, "data/congress-legislators/%s" % os.path.basename(f))
	for f in glob.glob("%s/congress-legislators/alternate_formats/*.csv" % SCRAPER_PATH):
		make_link(f, "data/congress-legislators/%s" % os.path.basename(f))

	# Convert people YAML into alternate formats.
	
	# Load YAML (directly) into db.
	os.system("./parse.py person") #  -l ERROR
	os.system("./manage.py update_index -v 0 -u person person")
	#os.system("./manage.py prune_index -u person person")
	
	# Save a fixture.
	os.system("./manage.py dumpdata --format json person > data/db/django-fixture-people.json")

if "committees" in sys.argv:
	if CONGRESS != 113: raise ValueErrror()
	
	# Committee metadata.
	
	# Pull latest YAML.
	os.system("cd %s/congress-legislators; git fetch -pq" % SCRAPER_PATH)
	os.system("cd %s/congress-legislators; git merge --ff-only -q origin/master" % SCRAPER_PATH)
	
	# Convert committee YAML into the legacy format.
	os.system(". %s/congress-legislators/scripts/.env/bin/activate; python ../scripts/legacy-conversion/convert_committees.py %s %s/congress-legislators/ ../data/us/%d/committees.xml" % (SCRAPER_PATH, SCRAPER_PATH, SCRAPER_PATH, CONGRESS))

	# Committee events.
	os.system("cd %s; . .env/bin/activate; ./run committee_meetings %s --log=%s" % (SCRAPER_PATH, fetch_mode, log_level))
	
	# Load into db.
	os.system("./parse.py -l ERROR committee")

do_bill_parse = False

if "text" in sys.argv:
	# Update the mirror of GPO FDSys.
	os.system("cd %s; . .env/bin/activate; ./run fdsys --collections=BILLS --store=mods,text,xml --log=%s" % (SCRAPER_PATH, log_level))

	# Update the mirror of Cato's deepbills.
	os.system("cd %s; . .env/bin/activate; ./run deepbills --log=%s" % (SCRAPER_PATH, log_level))
	
	# Glob all of the bill text files. Create hard links in the data directory to
	# their locations in the congress project data directoy.
	
	# We should start at 103 in case GPO has made changes to past files,
	# or 82 if we want to start with the statute-extracted text, but it
	# takes so long to go through it all!
	starting_congress = CONGRESS
	for congress in xrange(starting_congress, CONGRESS+1):
		mkdir("data/us/bills.text/%d" % congress)
		for bt in bill_type_map.values():
			mkdir("data/us/bills.text/%d/%s" % (congress, bt))
		
		for bill in sorted(glob.iglob("%s/data/%d/bills/*/*" % (SCRAPER_PATH, congress))):
			bill_type, bill_number = re.match(r"([a-z]+)(\d+)$", os.path.basename(bill)).groups()
			bill_type = bill_type_map[bill_type]
			for ver in sorted(glob.iglob(bill + "/text-versions/*")):
				basename = "../data/us/bills.text/%d/%s/%s%s%s." % (congress, bill_type, bill_type, bill_number, os.path.basename(ver))
				if congress >= 103:
					# Starting with GPO FDSys bill text, we'll pull MODS files
					# into our legacy location.
					make_link(ver + "/mods.xml", basename + "mods.xml")
				else:
					# For older bill text that we got from GPO FDSys Statutes
					# at Large, we don't have MODS but we do have text-only
					# bill text. Statutes only, of course. We have only 'enr'
					# versions, so immediately create the symlink from the
					# unversioned file name (representing most recent status)
					# to the enr version.
					basename2 = "../data/us/bills.text/%d/%s/%s%s." % (congress, bill_type, bill_type, bill_number)
					make_link(ver + "/document.txt", basename + "txt")
					if os.path.exists(basename2 + "txt"): os.unlink(basename2 + "txt")
					os.symlink(os.path.basename(basename + "txt"), basename2 + "txt")
	
	# Now do the old-style scraper (except mods files) because it handles
	# making symlinks to the latest version of each bill. And other data
	# types, like XML.

	# Scrape with legacy scraper.
	# Do this before bills because the process of loading into the db checks for new
	# bill text and generates feed events for text availability.
	os.system("cd ../scripts/gather; perl fetchbilltext.pl FULLTEXT %d" % CONGRESS)
	os.system("cd ../scripts/gather; perl fetchbilltext.pl GENERATE %d" % CONGRESS)
	do_bill_parse = True # don't know if we got any new files
	
if "bills" in sys.argv:
	# Scrape.
	os.system("cd %s; . .env/bin/activate; ./run bills --govtrack %s --congress=%d --log=%s" % (SCRAPER_PATH, fetch_mode, CONGRESS, log_level))
	
	# Copy files into legacy location.
	mkdir("data/us/%d/bills" % CONGRESS)
	bill_type_map = { 'hr': 'h', 's': 's', 'hres': 'hr', 'sres': 'sr', 'hjres': 'hj', 'sjres': 'sj', 'hconres': 'hc', 'sconres': 'sc' }
	for fn in sorted(glob.glob("%s/data/%d/bills/*/*/data.xml" % (SCRAPER_PATH, CONGRESS))):
		congress, bill_type, number = re.match(r".*congress/data/(\d+)/bills/([a-z]+)/(?:[a-z]+)(\d+)/data.xml$", fn).groups()
		if int(congress) != CONGRESS: raise ValueError()
		if bill_type not in bill_type_map: raise ValueError()
		fn2 = "data/us/%d/bills/%s%d.xml" % (CONGRESS, bill_type_map[bill_type], int(number))
		do_bill_parse |= copy(fn, fn2, r'updated="[^"]+"')
	
	# Generate summary files.
	os.system("cd /home/govtrack/scripts/gather; perl parse_status.pl SUMMARIES %d" % CONGRESS)
		
	# TODO: Even if we didn't get any new files, the bills parser also
	# scrapes docs.house.gov and the Senate floor schedule, so we should
	# also periodically make sure we run the scraper for that too.
	
	# os.system("./manage.py dumpdata --format json bill.BillTerm > data/db/django-fixture-billterms.json")

if do_bill_parse:
	# Load into db.
	os.system("./parse.py --congress=%d -l %s bill" % (CONGRESS, log_level))

	# bills and state bills are indexed as they are parsed, but to
	# freshen the index... Because bills index full text and so
	# indexing each time is substantial, set the TIMEOUT and
	# BATCH_SIZE options in the haystack connections appropriately.
	# ./manage.py update_index -v 2 -u bill bill

if "amendments" in sys.argv:
	# Scrape.
	os.system("cd %s; . .env/bin/activate; ./run amendments --govtrack %s --congress=%d --log=%s" % (SCRAPER_PATH, fetch_mode, CONGRESS, log_level))

	# Copy files into legacy location.
	mkdir("data/us/%d/bills.amdt" % CONGRESS)
	for fn in sorted(glob.glob("%s/data/%d/amendments/*/*/data.xml" % (SCRAPER_PATH, CONGRESS))):
		congress, chamber, number = re.match(r".*congress/data/(\d+)/amendments/([hs])amdt/(?:[hs])amdt(\d+)/data.xml$", fn).groups()
		if int(congress) != CONGRESS: raise ValueError()
		fn2 = "data/us/%d/bills.amdt/%s%d.xml" % (CONGRESS, chamber, int(number))
		copy(fn, fn2, r'updated="[^"]+"')
		
	# Load into db.
	os.system("./parse.py --congress=%d -l %s amendment" % (CONGRESS, log_level))

if "votes" in sys.argv:
	# Scrape.
	session = str(datetime.datetime.now().year)
	os.system("cd %s; . .env/bin/activate; ./run votes --govtrack %s --congress=%d --session=%s --log=%s" % (SCRAPER_PATH, fetch_mode, CONGRESS, session, log_level))
	
	# Copy files into legacy location.
	did_any_file_change = False
	mkdir("data/us/%d/rolls" % CONGRESS)
	for fn in sorted(glob.glob("%s/data/%d/votes/*/*/data.xml" % (SCRAPER_PATH, CONGRESS))):
		congress, session, chamber, number = re.match(r".*congress/data/(\d+)/votes/(\d+)/([hs])(\d+)/data.xml$", fn).groups()
		if int(congress) != CONGRESS: raise ValueError()
		fn2 = "data/us/%d/rolls/%s%s-%d.xml" % (CONGRESS, chamber, session, int(number))
		did_any_file_change |= copy(fn, fn2, r'updated="[^"]+"')
		
	# Load into db.
	if did_any_file_change or True: # amendments can mark votes as missing data
		os.system("./parse.py --congress=%d -l %s vote" % (CONGRESS, log_level))

if "stats" in sys.argv:
	os.system("analysis/sponsorship_analysis.py %d" % CONGRESS)
	os.system("analysis/missed_votes.py %d" % CONGRESS)
	
if "am_mem_bills" in sys.argv:
	# American Memory
	os.syste("for c in {6..42}; do echo $c; ./parse.py bill --force --congress=$c --level=warn; done")
	
if "stat_bills" in sys.argv:
	# Pull in statutes from the 85th-92nd Congress
	# via the GPO's Statutes at Large.
	
	os.system("cd %s; . .env/bin/activate; ./run fdsys --collections=STATUTE --store=mods --log=%s" % (SCRAPER_PATH, "warn")) # log_level
	os.system("cd %s; . .env/bin/activate; ./run statutes --volumes=65-86 --log=%s" % (SCRAPER_PATH, "warn")) # log_level
	os.system("cd %s; . .env/bin/activate; ./run statutes --volumes=87-106 --textversions --log=%s" % (SCRAPER_PATH, "warn")) # log_level
	
	# Copy bill metadata into our legacy location.
	# (No need to copy text-versions anywhere: we read it from the congress data directory.)
	for congress in xrange(82, 92+1):
		print congress, "..."
		
		# Copy files into legacy location.
		mkdir("data/us/%d/bills" % congress)
		for fn in sorted(glob.glob("%s/data/%d/bills/*/*/data.xml" % (SCRAPER_PATH, congress))):
			bill_type, number = re.match(r".*congress/data/\d+/bills/([a-z]+)/(?:[a-z]+)(\d+)/data.xml$", fn).groups()
			if bill_type not in bill_type_map: raise ValueError()
			fn2 = "data/us/%d/bills/%s%d.xml" % (congress, bill_type_map[bill_type], int(number))
			copy(fn, fn2, r'updated="[^"]+"')
			
		# Load into db.
		os.system("./parse.py --congress=%d bill" % congress) #  -l ERROR
		
if "photos" in sys.argv:
	# Pull in any new photos from the unitedstates/images repository.

	import person.models, os, shutil, yaml

	os.system("cd ../scripts/congress-images; git pull --rebase")

	src = '../scripts/congress-images/congress/original/'
	dst = 'data/photos/'

	# Get a list of GovTrack IDs and Bioguide IDs for which photos are provided
	# in the unitedstates/images repo. Only import photos of current Members of
	# Congress because I haven't reviewed older photos necessarily.
	bioguide_ids = [f[len(src):-4] for f in glob.glob(src + '*.jpg')]
	id_pairs = person.models.Person.objects.filter(
		bioguideid__in=bioguide_ids,
		roles__current=True)\
		.values_list('id', 'bioguideid')

	for govtrack_id, bioguide_id in id_pairs:
		# source JPEG & sanity check that it exists
		fn1 = src + bioguide_id + ".jpg"
		if not os.path.exists(fn1):
			raise IOError(fn1)

		# get required metadata
		metadata = yaml.load(open(fn1.replace("/original/", "/metadata/").replace(".jpg", ".yaml")))
		if metadata.get("name", "").strip() == "": raise ValueError("Metadata is missing name.")
		if metadata.get("link", "").strip() == "": raise ValueError("Metadata is missing link.")

		# check if the destination JPEG already exists and it has different content
		fn2 = dst + str(govtrack_id) + ".jpeg"
		if os.path.exists(fn2) and md5(fn1) != md5(fn2):
			# Back up the existing files first. If we already have a backed up
			# image, don't overwrite the back up. Figure out what to do another
			# time and just bail now. Check that we won't overwrite any files
			# before we attempt to move them.
			def get_archive_fn(fn):
				return fn.replace("/photos/", "/photos/archive/")
			files_to_archive = [fn2] + glob.glob(fn2.replace(".jpeg", "-*"))
			for fn in files_to_archive:
				if os.path.exists(get_archive_fn(fn)):
				 	raise ValueError("Archived photo already exists: " + fn)

			# Okay now actually do the backup.
			for fn in files_to_archive:
				print fn, "=>", get_archive_fn(fn)
				shutil.move(fn, get_archive_fn(fn))

		# Copy in the file.
		if copy(fn1, fn2, None):
			print fn1, "=>", fn2

			# Write the metadata.
			with open(fn2.replace(".jpeg", "-credit.txt"), "w") as credit_file:
				credit_file.write( (metadata.get("link", "").strip() + " " + metadata.get("name", "").strip() + "\n").encode("utf-8") )
	
			# Generate resized versions.
			for size_width in (50, 100, 200):
				size_height = int(round(size_width * 1.2))
				os.system("convert %s -resize %dx%d^ -gravity center -extent %dx%d %s"
					% (fn2, size_width, size_height, size_width, size_height,
						fn2.replace(".jpeg", ("-%dpx.jpeg" % size_width)) ))
