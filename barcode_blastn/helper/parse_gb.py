from datetime import datetime
from time import sleep
from typing import Any, Dict, Generator, List, Optional
from urllib.error import HTTPError, URLError
from django.contrib.auth.models import User
from barcode_blastn.models import Annotation, TaxonomyNode
from Bio import Entrez, SeqIO
from Bio.GenBank.Record import Reference
from Bio.SeqFeature import SeqFeature
from Bio.SeqIO import SeqRecord
from ratelimit import limits
from ratelimit.decorators import sleep_and_retry
from Bio.Seq import UndefinedSequenceError

# NCBI Rate limit without an API key is 3 requests per second
PERIOD = 1 # Time between calls, in number of seconds
ACCESSIONS_PER_REQUEST = 300 # Place a limit on the number of accession ids requested per request to GenBank
MAX_ACCESSIONS = 1500 # Place a limit on the number of accessions that can be added in one operation

class InsufficientAccessionData(BaseException):
    """Raised if the number of accessions returned from GenBank does not equal the number of accessions queried for."""
    missing_accessions: List[str] = []
    term: str = ''
    def __init__(self, missing_accessions: List[str], term: str = '', *args: object) -> None:
        super().__init__(*args)
        self.missing_accessions = missing_accessions
        self.term = term

class AccessionLimitExceeded(BaseException):
    """Raised if number of accessions is more than the maximum specified by MAX_ACCESSIONS"""
    max_accessions: int
    curr_accessions: int 
    def __init__(self, curr_accessions, max_accessions: int = MAX_ACCESSIONS, *args: object) -> None:
        super().__init__(*args)
        self.curr_accessions = curr_accessions
        self.max_accessions = max_accessions

class GenBankConnectionError(BaseException):
    """Raised if there was trouble connecting to NCBI GenBank"""
    queried_accessions: List[str] = []
    term = ''
    def __init__(self, queried_accessions: List[str], term='', *args: object) -> None:
        super().__init__(*args)
        self.queried_accessions = queried_accessions
        self.term = term

class TaxonomyConnectionError(BaseException):
    """Raised if there was trouble connecting to NCBI Taxonomy"""
    queried_ids: List[str] = []
    def __init__(self, queried_ids: List[str], *args: object) -> None:
        super().__init__(*args)
        self.queried_ids = queried_ids

def parse_gb_handle(handle) -> List[Dict[Any, Any]]:
    seq_records : Generator = SeqIO.parse(handle, "genbank")
    result: List[Dict[Any, Any]] = []

    seq_record : SeqRecord
    for seq_record in seq_records:
        try:
            dna_sequence = str(seq_record.seq)
        except UndefinedSequenceError:
            continue
        current_data : Dict = {
            'accession_number': seq_record.name,
            'definition': seq_record.description,
            'dna_sequence': dna_sequence,
            'version': seq_record.id,
            'keywords': ','.join(seq_record.annotations.get('keywords', [])),
            'journal': '',
            'authors': '',
            'title': '',
            'taxid': '',
            'taxonomy': ','.join(seq_record.annotations['taxonomy']),
            'genbank_modification_date': datetime.strptime(seq_record.annotations['date'], '%d-%b-%Y').date(),
            'taxon_superkingdom': None,
            'taxon_kingdom': None,
            'taxon_phylum': None,
            'taxon_class': None,
            'taxon_order': None,
            'taxon_family': None,
            'taxon_genus': None,
            'taxon_species': None,
        }

        # Get first reference to papers, publications, submissions, etc.
        if len(seq_record.annotations['references']) > 0:
            first_reference: Reference = seq_record.annotations['references'][0]
            current_data['journal'] = first_reference.journal
            current_data['authors'] = first_reference.authors
            current_data['title'] = first_reference.title

        # Parse feature data
        features : List[ SeqFeature ] = seq_record.features
        qualifiers_to_extract = ['organism', 'organelle', 'isolate', 'country', 'specimen_voucher', 'type_material', 'lat_lon', 'db_xref', 'identified_by', 'collected_by', 'collection_date']
        try: 
            source_feature : SeqFeature = [feature for feature in features if feature.type == 'source'][0]
        except IndexError:
            # set fields to N/A if no source features found
            for qualifier_name in qualifiers_to_extract:
                current_data[qualifier_name] = 'N/A'
        else:
            for qualifier_name in qualifiers_to_extract:
                if qualifier_name in source_feature.qualifiers:
                    try:
                        # take only the first line/element of the feature
                        if qualifier_name == 'db_xref':
                            # Standards for db_xref are here: https://www.insdc.org/submitting-standards/dbxref-qualifier-vocabulary/
                            for qualifier_value in source_feature.qualifiers[qualifier_name]:
                                (database, identifier) = qualifier_value.split(':')
                                # We are only concerned with references to NCBI Taxonomy
                                if database == 'taxon':
                                    current_data['taxid'] = identifier
                                    break
                        else:
                            current_data[qualifier_name] = source_feature.qualifiers[qualifier_name][0]
                    except IndexError:
                        # set it to 'error' if above code errored
                        current_data[qualifier_name] = 'error'
                else:
                    # set it to empty string if qualifier was not found in the data
                    current_data[qualifier_name] = ''

            # use type material specified in 'note' if no type_material was found 
            if (current_data['type_material'] == '' and 'note' in source_feature.qualifiers):
                notes : str = ''
                try:
                    # include all notes 
                    notes = "\n".join(source_feature.qualifiers['note'])
                except BaseException:
                    notes = 'error'
                else:
                    notes_lower = notes.lower()
                    if 'paratype' in notes_lower or 'holotype' in notes_lower:
                        if notes_lower.startswith('type: ') and len(notes_lower) > 6:
                            # remove "type: " from the beginning if it is present
                            notes = notes[6:]
                        else:
                            # print a warning to the console if the "type: " string was not found at start
                            print(f'Inferred type material from notes since it contained "paratype" or "holotype". It did not start with "type: ". Consider checking /type_material and/or /note info for {seq_record.name} in GenBank.')
                finally:
                    current_data['type_material'] = notes
        finally:
            result.append(current_data)

    return result

@sleep_and_retry
@limits(calls = 1, period = PERIOD)
def send_gb_request(accession_numbers: List[str] = [], raise_if_missing: bool = False, **kwargs) -> List[Dict[Any, Any]]:
    """
        Create and send a single web request to GenBank requesting the specified accession identifiers.

        Kwargs specified are passed to `Entrez.efetch()`
        
    Raises:

        GenBankConnectionError: If there is a network error when retrieving data from GenBank using Bio.Entrez

        InsufficientAccessionData: If the number of records sent by GenBank does not match the number of accession numbers requested. This indicates that there are accession numbers that do not match with a GenBank record.
    """
    request_time_str = datetime.now().strftime("%H:%M:%S.%f")
    if len(accession_numbers) > 0:
        kwargs['id'] = ','.join(accession_numbers)
    
    print(f'{request_time_str} | Fetching from GenBank with parameters {str(kwargs)}')
    term = kwargs.get('term', '')
    accs = accession_numbers

    # Raises URLError if there is a network error
    try:
        handle = Entrez.efetch(db="nucleotide", rettype="gb", retmode="text", **kwargs)
    except HTTPError as exc:
        ex = exc
        accs = accession_numbers
        if exc.code == 400:
            raise InsufficientAccessionData(missing_accessions=accession_numbers, term= kwargs.get('term', ''))
        else:
            raise GenBankConnectionError(queried_accessions=accession_numbers, term=kwargs.get('term', ''))
    except:
        raise GenBankConnectionError(queried_accessions=accession_numbers, term=kwargs.get('term', ''))
    response_time_str = datetime.now().strftime("%H:%M:%S.%f")
    print(f'{response_time_str} | Response received from GenBank for parameters {str(kwargs)}')

    result = parse_gb_handle(handle=handle)
    
    # Check if we are missing any data
    if raise_if_missing:
        successful_queries : List[str]
        successful_queries = [entry["accession_number"] for entry in result]
        successful_queries.extend([entry["version"] for entry in result])
        failed_queries = [d for d in accession_numbers if d not in successful_queries]
        # stop execution if data not complete
        if len(failed_queries) > 0:
            raise InsufficientAccessionData(failed_queries)

    # Close the data handle
    handle.close()
    return result

@sleep_and_retry
@limits(calls = 1, period = PERIOD)
def retrieve_gb(accession_numbers: List[str], term: Optional[str] = None, raise_if_missing: bool = False) -> List[Dict[str, Any]]: 
    """
        Request sequences record GenBank by requesting the specified accession identifiers or running search terms
        and return a list of records indexed by the accession id.
        Data is requested in batches, depending on the number of records requested, in order to comply with GenBank API limits. There is a hard
        limit on the number of records requested with one call, specified by MAX_ACCESSIONS
        
    Raises:
        ValueError: If no accession identifiers are provided.

        GenBankConnectionError: If there is a network error when retrieving data from GenBank using Bio.Entrez

        AccessionLimitExceeded: If the number of accessions is more than the maximum specified by MAX_ACCESSIONS

        InsufficientAccessionData: If the number of records sent by GenBank does not match the number of accession numbers requested. This indicates that there are accession numbers that do not match with a GenBank record.
    """
    if len(accession_numbers) == 0 and term is None:
        raise ValueError(f'List of accession numbers and search terms to query is empty.')
    # Limit the number of accessions that can be added in this operatio
    if len(accession_numbers) > MAX_ACCESSIONS:
        raise AccessionLimitExceeded(curr_accessions=accession_numbers, max_accessions=MAX_ACCESSIONS)

    desired_numbers = list(set(accession_numbers))

    Entrez.email = "william.huang1212@gmail.com"
    Entrez.max_tries = 1
    Entrez.tool = "barrel"

    # aggregate all data across all batches in a list
    all_data: List[Dict[str, Any]] = []
    if not term is None:
        term = term.strip()
        if len(term) > 0:
            # Run a ESearch to identify how many records there are
            try:
                search_handle = Entrez.esearch(db='nucleotide', term=term, retmax=20, usehistory='y')
                resp = Entrez.read(search_handle)
                search_handle.close()
                count: int = int(resp['Count'])
                webenv: str = resp['WebEnv']
                query_key: str = resp['QueryKey']
            except BaseException:
                print(f'{datetime.now()} | Error received from NCBI GenBank ESearch for term {term}')
                raise GenBankConnectionError(queried_accessions=[], term=term)
            else:
                print(f'{datetime.now()} | Data successfully received NCBI GenBank ESearch for term {term}. {count} sequences found.')
            
            # Limit the number of accessions that can be added in this operation
            if count > MAX_ACCESSIONS:
                raise AccessionLimitExceeded(curr_accessions=count, max_accessions=MAX_ACCESSIONS)
            retmax = ACCESSIONS_PER_REQUEST # Max number of records per batch
            retstart = 0 # Starting index of the current batch
            # Retrieve the sequence records in batches, using the web environment and query keys from the ESearch
            while count > 0:
                all_data.extend(send_gb_request(raise_if_missing=False, webenv=webenv, retstart=retstart, retmax=retmax, query_key=query_key))
                retstart = retstart + retmax
                count = count - retmax
                sleep(1)

    # keep track of batch numbers
    batch_no = 1
    # Request all the data in batches
    while len(desired_numbers) > 0:
        # Pop the first n number of accession numbers and form a query string
        next_numbers = desired_numbers[:ACCESSIONS_PER_REQUEST]
        desired_numbers = desired_numbers[ACCESSIONS_PER_REQUEST:] 
        batch_no = batch_no + 1
        batch_result = send_gb_request(next_numbers, raise_if_missing=raise_if_missing)
        all_data.extend(batch_result)
        # If we require another batch, wait some number of seconds
        if len(desired_numbers) > 0:
            sleep(1)

    return all_data

def get_rank(ncbi_rank: str) -> str:
    '''
    Given the taxonomic rank given by Entrez efetch, return the corresponding TaxonomyRank value
    for the database.

    If given rank does not match, return an empty string ('').
    '''
    if ncbi_rank == 'superkingdom':
        return TaxonomyNode.TaxonomyRank.SUPERKINGDOM
    elif ncbi_rank == 'kingdom':
        return TaxonomyNode.TaxonomyRank.KINGDOM
    elif ncbi_rank == 'phylum':
        return TaxonomyNode.TaxonomyRank.PHYLUM
    elif ncbi_rank == 'class':
        return TaxonomyNode.TaxonomyRank.CLASS
    elif ncbi_rank == 'order':
        return TaxonomyNode.TaxonomyRank.ORDER
    elif ncbi_rank == 'family':
        return TaxonomyNode.TaxonomyRank.FAMILY
    elif ncbi_rank == 'genus':
        return TaxonomyNode.TaxonomyRank.GENUS
    elif ncbi_rank == 'species':
        return TaxonomyNode.TaxonomyRank.SPECIES
    else:
        return ''

@sleep_and_retry
@limits(calls = 1, period = PERIOD)
def save_taxonomy(taxonomy_info: List[Dict[str, Any]], user: Optional[User]) -> List[Dict[str, Any]]:
    """
        Retrieve taxonomic lineages from NCBI taxonomy and save taxonomic ranks into the database.
    """

    # Extract list of taxonomic ids from genbank data
    taxids = [gb['taxid'] for gb in taxonomy_info]
    query_ids: List[str] = list(set(taxids))
    query_string: str = ','.join(query_ids)

    # Don't bother with retrieving taxonomy data if there are no taxonomy cross-references
    if len(query_string) == 0:
        taxa_data = {}
    else:
        print(f'{datetime.now()} | Fetching from NCBI Taxonomy for ids {query_string}')
        try:
            Entrez.email = "william.huang1212@gmail.com"
            Entrez.max_tries = 1
            Entrez.tool = "barcode_identifier"
            taxonomy_handle = Entrez.efetch(db='taxonomy', id=query_string, retmode='xml')
        except BaseException:
            print(f'{datetime.now()} | Error received from NCBI Taxonomy for ids {query_string}')
            raise TaxonomyConnectionError(query_ids)
        else:
            print(f'{datetime.now()} | Data successfully received from NCBI Taxonomy for ids {query_string}')
        
        response_data = Entrez.parse(taxonomy_handle)
        response_data = [t for t in response_data]
        taxa_data = [t for t in response_data]
        taxids = [t['TaxId'] for t in response_data]
        taxa_data = dict(zip(taxids, taxa_data))
        taxonomy_handle.close()

    taxids = {}

    for i in range(len(taxonomy_info)):
        entry = taxa_data.get(taxonomy_info[i]['taxid'], None)
        if entry is None:
            taxonomy_info[i]['taxid'] = -2
            continue
        else:
            taxonomy_info[i]['taxid'] = int(taxonomy_info[i]['taxid'])
        lineage = entry['LineageEx']
        for level in lineage:
            id = level['TaxId']
            if id in taxids:
                continue
            rank = level['Rank']
            key = 'taxon_species'
            if rank == 'superkingdom':
                rank = TaxonomyNode.TaxonomyRank.SUPERKINGDOM
                key = 'taxon_superkingdom'
            elif rank == 'kingdom':
                rank = TaxonomyNode.TaxonomyRank.KINGDOM
                key = 'taxon_kingdom'
            elif rank == 'phylum':
                rank = TaxonomyNode.TaxonomyRank.PHYLUM
                key = 'taxon_phylum'
            elif rank == 'class':
                rank = TaxonomyNode.TaxonomyRank.CLASS
                key = 'taxon_class'
            elif rank == 'order':
                rank = TaxonomyNode.TaxonomyRank.ORDER
                key = 'taxon_order'
            elif rank == 'family':
                rank = TaxonomyNode.TaxonomyRank.FAMILY
                key = 'taxon_family'
            elif rank == 'genus':
                rank=TaxonomyNode.TaxonomyRank.GENUS
                key = 'taxon_genus'
            elif rank == 'species':
                rank=TaxonomyNode.TaxonomyRank.SPECIES
                key = 'taxon_species'
            else:
                continue

            object: TaxonomyNode
            # search if already exists
            object, created = TaxonomyNode.objects.get_or_create(id=id, defaults={
                'rank': rank,
                'scientific_name': level['ScientificName']
            })               
            taxonomy_info[i][key] = object

        # assign species based on outermost taxon
        object, created = TaxonomyNode.objects.get_or_create(id=entry['TaxId'], defaults={
                'rank': TaxonomyNode.TaxonomyRank.SPECIES,
                'scientific_name': entry['ScientificName']
            })
        taxonomy_info[i]['taxon_species'] = object
    
        # Check for taxonomic uncertainty
        lineage = taxonomy_info[i].get('taxonomy', '') 
        keywords = ['cf.', 'aff.', 'sp.', 'environment', 'undescribed', 'uncultured', \
            'complex', 'unclassified', 'nom.', 'nud.', 'unidentif']

        for keyword in keywords:
            if keyword in lineage or keyword in taxonomy_info[i]['definition']:
                annotations = taxonomy_info[i].get('create_annotations', [])
                annotations.append({
                    'annotation_type': Annotation.AnnotationType.UNRESOLVED_TAXONOMY,
                    'comment': f'(Auto-annotation by Barrel) Potential taxonomic uncertainty due to presence of "{keyword}" string within lineage or definition.'
                })
                taxonomy_info[i]['create_annotations'] = annotations
        taxonomy_info[i]['annotation_user'] = user

    return taxonomy_info

