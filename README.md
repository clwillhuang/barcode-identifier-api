# Barcode Identifier API

The Barcode Identifier API is a work-in-progress web API that allows users to BLAST against sequences in customized databases.

Built in Python with the Django Rest Framework.

Documentation will be made available when the project nears completion.

## First time installation

### Download the code from GitHub

Clone from the repository:
```
git clone https://github.com/<username>/barcode-identifier-api.git
cd barcode-identifier-api/
```

Or, if the repo is private, follow the following format:
```
git clone https://username:<token>@github.com/<username>/barcode-identifier-api.git
cd barcode-identifier-api/
```

Install the required Linux packages through the terminal:
```
sudo apt install postgresql postgresql-contrib
sudo apt install libpq-dev
sudo apt install libcurl4-openssl-dev libssl-dev
sudo apt-get install libpcre3 libprce3-dev 
```

### Set up the Python virtual environment (only if local machine)

This package was built using Python 3.8.10. I recommend using the pyenv package to maintain multiple python versions installed on my machine. WARNING: use of pyenv will change how `python` is resolved in the terminal. You can check where `python` currently points to in the terminal with the command `which python`. The following uses pyenv to install and make a virtual environment for Python 3.8.10:
```
# check version
python --version

# recommend installing pyenv 
curl https://pyenv.run | bash
exec $SHELL

# install 3.8.10 using pyenv
pyenv install 3.8.10
pyenv global 3.8.10

# make the virtual env
python -m venv env_barcode
source env_barcode/bin/activate
``` 

Finally, you should be able to install the correct python modules.
```
pip install -r requirements.txt
```

### Set up the Python virtual environment (only if AWS EC2) (not complete)
Make the virtual environment, then activate it.
```
python3 -m venv env_barcode 
source env_barcode/bin/activate
# check the python version. This guide used Python 3.10.6
python --version
```

Finally, you should be able to install the correct python modules.
```
pip install -r requirements.txt
```

### Install the nginx web server (not complete) 
Install nginx in the terminal:
```
sudo apt-get install nginx 
```

For the nginx configuration file, we use We can use the file at [/nginx/barcode_identifier_api_nginx.conf](/nginx/barcode_identifier_api_nginx.conf) as a starting point. Update the server_name to either the IP address of the AWS EC2 instance or the domain on which the API is served at through the terminal,
```
# run in terminal:
sudo nano nginx/barcode_identifier_api_nginx.conf

# then change update a line that looks like this, where the variable is substituted with an ip address (e.g. 18.117.128.142) or domain (e.g. example.com)
# server_name <update_this>
```

Then copy the configuration file to the nginx directory, and restart nginx to update.
```
# copy file 
sudo cp ./nginx/barcode_identifier_api_nginx.conf /etc/nginx/sites-available/
# symlink 
sudo ln -s /etc/nginx/sites-available/barcode_identifier_api_nginx.conf /etc/nginx/sites-enabled/
# restart nginx to apply changes
sudo /etc/init.d/nginx restart
```

Next we must setup the nginx configuration file. Edit the nginx configuration folder by updating two lines below. An example nginx.conf file is saved at [/nginx/nginx.conf](/nginx/nginx.conf) for reference.
```
sudo nano /etc/nginx/nginx.conf 
# update value for 'user' to 'www-data' 
# update value for 'worker_processes' to '1'
```

Let's finish enabling HTTPS on our site (i.e. encryption) by enabling SSL/TSL below through one of the two options below:

#### Option 1: You are in a development environment and can use a self-signed certificate

We are following the procedure suggested by the [uWSGI documentation](https://uwsgi.readthedocs.io/en/latest/HTTPS.html) to generate a __self-signed__ certificate. Most browsers will not trust self-signed certificate, and may even prevent you from accessing the page. **Use only for development, not production.**

We will generate our key and certificate at ~/cert.
```
cd ~
mkdir ~/cert
cd ~/cert 
openssl genrsa -out foobar.key 2048
openssl req -new -key foobar.key -out foobar.csr
openssl x509 -req -days 365 -in foobar.csr -signkey foobar.key -out foobar.crt
```
According our nginx configuration, the certificates must be moved to /etc/nginx/ssl
```
sudo cp ~/cert/** /etc/nginx/ssl/
```

#### Option 2: Setting up HTTPS with Let's Encrypt and Certbot

**The writing for this section has not been completed.**

**Prerequisite: you must have a custom domain ready, in order to setup our SSL certificate. The initial EC2 *.amazonaws.com domain will not work.**

The following information is the first few steps for the process of obtaining and installing an SSL certificate for the domain through "Lets Encrypt" and "Certbot".

We will be using [CertBot with its instructions given here](https://certbot.eff.org/instructions?ws=nginx&os=ubuntufocal).

```
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot
```

Start Certbot to get our certificate.
```
# run certbot, allowing it to automatically update your nginx configuration 
sudo certbot --nginx
# alternative: just get our certificate
# sudo certbot certonly --nginx
```

### Install uWSGI (not complete)
uWSGI should be already installed when we installed our python packages with pip

Set the following global privileges to the /home/ubuntu folder, which is very permissive but an alternative solution has yet to be found.
```
sudo chmod 755 /home/ubuntu
```

### Install celery

The celery worker allows the BLAST searches to be queued up and run separately, in queue order, from the app. This section assumes that you have already configured a Simple Queue System on AWS, named `BarcodeQueue.fifo`. 

We must ensure that celery has sufficient permissions to run its tasks. Add a new celery user to the instance, which automatically adds it to a new "celery" group.
```
# make our celery user 
sudo adduser celery
# add the default ec2 user (e.g. ubuntu) to the celery group 
sudo usermod -a -G celery ubuntu
```

Make folders for celery to write its pid and perform error logging, and give permissions for celery to both directories 
```
sudo mkdir /var/run/celery
sudo chown celery:celery /var/run/celery
sudo mkdir /var/log/celery
sudo chown celery:celery /var/run/celery
```

# refresh services and restart celery
systemctl start celery.service 
systemctl start emperor.uwsgi.service

### Install blastn
Download and install ncbi blast tool to the project directory. The API was originally built for ncbi-blast-2.12.0
```
# download from ncbi 
wget https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/2.12.0/ncbi-blast-2.12.0+-x64-linux.tar.gz
# unzip
tar -xzvf ncbi-blast-2.12.0+-x64-linux.tar.gz
# delete original download file 
rm ncbi-blast-2.12.0+-x64-linux.tar.gz
```

If not already done so, make the blast db file used to run the queries. FIRST, make a database.fasta file at ./4f33c746-e566-4cfb-a79d-1d4bcb8cae6d/database.fasta, and add fasta entries for every species in the database. Then, run the following command in the terminal to make the db:
```
ncbi-blast-2.12.0+/bin/makeblastdb -in fishdb/4f33c746-e566-4cfb-a79d-1d4bcb8cae6d/database.fasta -dbtype nucl -out fishdb/4f33c746-e566-4cfb-a79d-1d4bcb8cae6d/database -title database 
```

### Setup the systemd services

We will be using `systemd` in order to run uWSGI and Celery to run in the background. Copy the files for these two services to the correct directory:
```
# copy service for uwsgi
sudo cp ~/barcode_identifier_api/emperor.uwsgi.service /etc/systemd/system/
# copy service for celery
sudo cp ~/barcode_identifier_api/celery.service /etc/systemd/system/
```

### Set up the SQL database 

Set up the PostGre SQL database:
```
sudo service postgresql start
sudo su - postgres
psql
CREATE DATABASE barcode_identifier_api
CREATE USER admin with PASSWORD '<password>'
GRANT ALL PRIVILEGES ON DATABASE barcode_identifier_api TO admin
\q
exit
```

Optionally, if you want the database to be populated with data from a local `db.sql` file:
```
psql -d barcode_identifier_db -f db.sql -U admin
```

Set-up the connection to Amazon Simple Queue Service (SQS) by adding the credentials to the local environment variables or the `settings.py` file. In the settings.py file, these should correspond to the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` keys, as well as `access_key_id` and `secret_access_key`. 

## Running the API
If the AWS EC2 instance was restarted, ensure that the nginx conf file at /etc/nginx/sites-available is updated. For example, the `server_name` directive must be changed to the new domain name or IP address of the instance.
```
# open the file and edit as necessary
sudo nano /etc/nginx/sites-available/barcode_identifier_api_nginx.conf 

# restart nginx to apply changes.
sudo /etc/init.d/nginx restart
```

Start the SQL database in the terminal.
```
sudo service postgresql start
```

There are two options for how to run the app. 

### Production

The following procedure starts the app and worker as two services running in the background. Closing the terminal will not stop the services, so the instance will still be active and receiving web requests.

```
systemctl start emperor.uwsgi.service
systemctl start celery.service
```

You can verify that the processes are working by inspecting the log files at [barcode_identifier_api.log] (for the app) and `/var/log/celery/worker.log` and `/var/log/celery/worker-1.log`. 

To stop the app or the worker, issue the corresponding command in the console.
```
systemctl stop emperor.uwsgi.service
systemctl stop celery.service 
```

### Development 

The following procedure starts the app and the worker, each in a different terminal. Once the terminal is closed, both the app and worker close.

Open a terminal and run the API server. This is the web server that serves the API and allows the user to access information.
```
uwsgi --ini barcode_identifier_api_uwsgi.ini
```

Open another terminal and run the worker queue used to perform the BLAST searches. 
```
celery -A barcode_identifier_api worker --loglevel=INFO -Q BarcodeQueue.fifo
```

To stop the app or the worker, press `Ctrl-C` in the appropriate terminal.

## Downloading database data
In the event that the database should be dumped/downloaded to a file, run the following in the terminal to create a `db.sql` file which can be transferred.
```
pg_dump -U admin -h 127.0.0.1 barcode_identifier_db > db.sql
```

## Troubleshooting

### I get the error `psql: error: FATAL:  Peer authentication failed for user "admin"` in the terminal when I try to run `psql`
If you are accessing `psql` through the terminal, try specifying the port number and the database to connect to. Example:
```
psql -d barcode_identifier_db -U admin -h 127.0.0.1
```

In the event that Postgre SQL still fails to authenticate you (e.g.: `psql: error: FATAL:  Peer authentication failed for user "admin"` or `psql: error: FATAL:  role "linux_username" does not exist`), one remedy is to change the authentication settings of Postgre SQL (requires root access) so that the `postgres` username is usable. Details on how to do so are [online](https://stackoverflow.com/a/26735105). After, running `psql` in the terminal requires you to specify the username explicitly with `-U postgres`. You can check whether you can log into the database by running `psql -U <username> <database>`.

### I get the error `django.db.utils.ProgrammingError: permission denied for table django_migrations` when running `python manage.py makemigrations`
This arises when the local PostGre SQL database is not owned by the the `admin` user which the API access the database from. We have to regrant permissions to the `admin` user, from some user second_user with higher permissions to the database (this second user is likely `postgres`).
```
# log into the database with a user with greater permissions
psql -U <second_user> -h 127.0.0.1 barcode_identifier_db

# in postgres:
GRANT ALL ON ALL TABLES IN SCHEMA public to admin;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public to admin;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public to admin;
```
Now the error should be resolved, and commands like `python manage.py runserver` should work.

### I get errors when syncing the local database with the `db.sql` file with `psql -f db.sql`
One method of resolving it, albeit very blunt and destructive, is to remove all the data in the table and let the database be constructed from scratch using the `db.sql` file.
```
python manage.py flush
python manage.py migrate barcode_blastn zero
psql -d barcode_identifier_db -f db.sql -U admin -h 127.0.0.1 
```

Alternatively, you can drop the database and recreate it:
```
sudo su postgres
psql
drop database your_database_name;
create database your_database_name with owner user_you_use_in_django;
\q
exit
python manage.py makemigrations
python manage.py migrate
psql -h 127.0.0.1 -d barcode_identifier_db -U admin -f db.sql
```

### I started the app as a background daemon. How do I kill the process?
If the `barcode_identifier_api_uwsgi.ini` file specified the app to run in the background, you cannot kill the app with `Ctrl-C` as usual. Double check that the process is running, then kill it with pkill.
```
# check if process running
ps aux 
# kill uwsgi processes
sudo pkill -f uwsgi -9
```



