from datetime import datetime, timezone
import os
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple
from barcode_blastn.file_paths import get_data_fishdb_path, get_data_library_path, get_ncbi_folder
from barcode_blastn.helper.parse_gb import GenBankConnectionError, InsufficientAccessionData, retrieve_gb, save_taxonomy
from barcode_blastn.models import Annotation, BlastDb, Hit, Library, NuccoreSequence
from barcode_blastn.serializers import NuccoreSequenceSerializer 
from django.db.models import QuerySet, Value, Q
from django.db.models.functions import Length, Replace, Upper

class SequenceUpdateSummary:
    no_change = []
    accession_version_changed = []
    metadata_changed = []
    deleted = []
    added = []

def compareHits(hitA: Hit, hitB: Hit):
    '''Returns:
    -   1 if hitB is a better hit than hitA for the purposes of
    taxonomic classification.
    -   -1 if hitA is better than hitB.
    -   0 if both hits identical
    '''
    if hitA.percent_identity < hitB.percent_identity:
        return 1
    elif hitA.percent_identity > hitB.percent_identity:
        return -1
    else:
        return 0

def delete_blastdb(blast_db: Optional[BlastDb]) -> None:
    '''
    Delete a BLAST database. Also remove its files from the filesystem.

    Raises ValueError if value is None

    Raises OSError if error encountered while deleting files.
    '''
    if blast_db is None:
        raise ValueError('Library cannnot be None')

    # delete the database from the file system
    try:
        local_db_folder = get_data_fishdb_path(blast_db)
        if len(str(blast_db.id)) > 0 and os.path.exists(local_db_folder):
            shutil.rmtree(local_db_folder, ignore_errors=True)
    except BaseException as exc:
        raise OSError(exc)
    else:
        blast_db.delete()

def delete_library(library: Optional[Library]):
    '''
    Shared logic for deleting a reference library

    Raises ValueError if value is None

    Raises OSError if error encountered while deleting files.
    '''
    if library is None:
        raise ValueError('Library cannnot be None')

    dbs = BlastDb.objects.filter(library=library)
    db: BlastDb
    for db in dbs:
        delete_blastdb(db)
    library.delete()     

def save_blastdb(obj: BlastDb, perform_lock: bool = False) -> BlastDb:
    '''
    Save the BlastDb. The BlastDb should have accession numbers **added and saved**, reference library indicated.

    If the database is to be locked, the save process includes:
    - locking the database, i.e. setting locked to True
    - assign a version number if it was previously locked, based on the latest library version
    - setting the ._change_reason to "Lock database" or equivalent, for the change history
    - calling `.save()` on the instance

    Raises:
        OSError: If an error is encountered while manipulating files in the filesystem when preparing the database files. 
    '''
    if perform_lock:
        lastPublished: Optional[BlastDb] = BlastDb.objects.latest(obj.library)
        version_nums = (1, 0, 0) # if this is the first published version of the library, assign version 1.1.1 

        # If a previous version exists, assign the new database a version reflective
        # of the differences of it from the most recent database
        if lastPublished is not None:
            sequence_summary: SequenceUpdateSummary = calculate_update_summary(last=lastPublished, current=obj)
            if len(sequence_summary.deleted) > 0 or len(sequence_summary.added) > 0 or len(sequence_summary.accession_version_changed) > 0:
                version_nums = (lastPublished.genbank_version + 1, 0, 0)
            elif len(sequence_summary.metadata_changed) > 0:
                version_nums = (lastPublished.genbank_version, lastPublished.major_version + 1, 0)
            else:
                version_nums = (lastPublished.genbank_version, lastPublished.major_version, lastPublished.minor_version + 1)

        # Make database files
        # Make the database from scratch
        library_path = get_data_library_path(obj.library)
        if not os.path.exists(library_path):
            os.mkdir(library_path)
        elif os.path.isfile(library_path):
            os.remove(library_path)
            os.mkdir(library_path)
        fishdb_path = get_data_fishdb_path(obj)
        # make a directory for the database if it doesn't exist
        if not os.path.exists(fishdb_path):
            os.mkdir(fishdb_path)
        # if the directory exists, delete the old folder
        else:
            try:
                # delete the old folder
                shutil.rmtree(fishdb_path, ignore_errors = False)
            except BaseException:
                raise OSError('Encountered error while setting up database files.')
            os.mkdir(fishdb_path)
        
        fasta_file = fishdb_path + f'/database.fasta'
        sequences: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=obj)
        with open(fasta_file, 'w') as my_file:
            for x in sequences:
                sequence_identifier = x.version
                dna_sequence = x.dna_sequence
                my_file.write('>' + sequence_identifier + '\n' + dna_sequence + '\n')
        
        my_file.close()

        ncbi_blast_version = 'ncbi-blast-2.12.0+'
        blast_root = get_ncbi_folder(ncbi_blast_version=ncbi_blast_version)
        command = '{} -in {} -dbtype nucl -out {} -title {} -parse_seqids'.format(blast_root + '/makeblastdb', fasta_file, fishdb_path + '/database', 'database')
        
        os.system(command)

        obj.genbank_version = version_nums[0]
        obj.major_version = version_nums[1]
        obj.minor_version = version_nums[2]
        obj.locked = True
        new_change_reason = ['Locked database'] 
        if len(obj._change_reason) > 0:
            new_change_reason.append(obj._change_reason)
        obj._change_reason = ', '.join(new_change_reason)

    obj.save()

    return obj

def create_blastdb(additional_accessions: List[str], base: Optional[BlastDb] = None, database: Optional[BlastDb] = None, search_term: Optional[str] = None, min_length: int = -1, max_length: int = -1, max_ambiguous_bases = -1, blacklist: List[str] = [], require_taxonomy: bool = False, **kwargs) -> BlastDb:
    '''
    Create a new blastdb with the accession numbers in additional_accessions. Also add the accession numbers
    from the instance given by `base`.
    The accession numbers provided by base and additional accessions are able to overlap, as the number list
    is filtered for unique values before addition.
    If `database` is given, the values and accessions will be populated into that instance and a new instance
    will not be created
    Data for every accession, from both additional_accessions and base, will be refetched from GenBank.
    The new blastdb and accessions will be saved. The saved instance will be returned.

    Returns the new database.

    Raises various exceptions (ValueError, GenBankConnectionError, AccessionLimitExceeded, InsufficientAccessionData) if errors encountered while fetching data from GenBank.

    Kwargs passed are passed onto the BlastDb constructor. If locked is set to True, 
    `save_blastdb()` is also called for the database to be locked.
    '''
    genbank_version = kwargs.pop('genbank_version', 0)
    major_version = kwargs.pop('major_version', 0)
    minor_version = kwargs.pop('minor_version', 0)
    created = kwargs.pop('created', datetime.now())
    locked = kwargs.pop('locked', False) # ignore value of locked for now
    if database is None:
        new_database: BlastDb = BlastDb(genbank_version=genbank_version, major_version=major_version, minor_version=minor_version, created=created, locked=False, **kwargs)
    else:
        new_database = database
        new_database.genbank_version = genbank_version
        new_database.major_version = major_version
        new_database.minor_version = minor_version
        new_database.created = created
        new_database.locked = False
        for k, v in kwargs.items():
            setattr(new_database, k, v)
    database._change_reason = 'Initial save'
    save_blastdb(new_database, perform_lock=False)
    
    accessions_to_add = additional_accessions
    seqs: Optional[QuerySet[NuccoreSequence]]
    if base is not None:
        seqs = NuccoreSequence.objects.filter(owner_database=base)
        # Add the base database's accessions to the list of accessions to retrieve
        accessions_to_add.extend([s.version for s in seqs])
    else:
        seqs = None

    # ensure all accessions are unique
    accessions_to_add = list(set(accessions_to_add))
    # add sequences if there are accessions to add
    if len(accessions_to_add) > 0 or (not search_term is None and len(search_term) > 0):
        new_sequences = add_sequences_to_database(new_database, desired_numbers=accessions_to_add, search_term=search_term, min_length=min_length, max_length=max_length, max_ambiguous_bases=max_ambiguous_bases, blacklist=blacklist, require_taxonomy=require_taxonomy)
        if not seqs is None:
            # Carry over the annotations from the old database
            annotations_to_save = []
            sequence: NuccoreSequence
            for sequence in new_sequences:
                try:
                    existing: NuccoreSequence = seqs.get(version=sequence.version)
                except NuccoreSequence.DoesNotExist:
                    # Skip annotations if the sequence was not from base database
                    pass
                else:
                    # Get all annotations from the old sequence entry
                    old_annotations = existing.annotations.all()
                    old: Annotation
                    # Clone all old annotations
                    for old in old_annotations:
                        annotations_to_save.append(
                            Annotation(
                                sequence=sequence,
                                poster=old.poster,
                                annotation_type=old.annotation_type,
                                comment=old.comment
                            )
                        )
            # Bulk save all annotations
            Annotation.objects.bulk_create(annotations_to_save)

    # If the database is to be locked, lock it. Else just return
    if locked:
        new_database = save_blastdb(new_database, perform_lock=locked)
    return new_database

def calculate_update_summary(last: BlastDb, current: BlastDb) -> SequenceUpdateSummary:
    '''
    Create a SequenceUpdateSummary based on the differences of current database
    from the last database.
    '''

    fields_to_check = ['definition', 'dna_sequence', 'organism', 'organelle', 'isolate', 'country', 'specimen_voucher', 'type_material', 'lat_lon']

    last_sequences: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=last)
    current_sequences: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=current)

    summary: SequenceUpdateSummary = SequenceUpdateSummary()

    an_to_last: Dict[str, NuccoreSequence] = {}
    an_to_current: Dict[str, NuccoreSequence] = {}
    seq: NuccoreSequence
    for seq in current_sequences:
        an_to_current[seq.accession_number] = seq
    for seq in last_sequences:
        an_to_last[seq.accession_number] = seq
        if seq.accession_number not in an_to_current:
            summary.deleted.append(seq.accession_number)
    
    for current_seq in current_sequences:
        if current_seq.accession_number not in an_to_last:
            summary.added.append(current_seq.accession_number)
        else:
            instance = an_to_last[current_seq.accession_number]
            if current_seq.version != instance.version or current_seq.dna_sequence != instance.dna_sequence:
                summary.accession_version_changed.append(current_seq.accession_number)
            else:
                has_any_field_changed: bool = False
                for field in fields_to_check:
                    if not has_any_field_changed and getattr(current_seq, field) != getattr(instance, field):
                        has_any_field_changed = True 
                
                if has_any_field_changed:
                    summary.metadata_changed.append(instance.accession_number)
                else:
                    summary.no_change.append(instance.accession_number)
    return summary

def update_from_genbank(sequence_instances: QuerySet[NuccoreSequence]) -> SequenceUpdateSummary:
    '''
    Update a query set of existing and saved NuccoreSequence objects by retrieving new GenBank data and saving the data back to the database.
    If new GenBank data cannot be found for an existing instance accession number, then the instance is deleted.

    Returns a summary of the sequences updated.
    '''
    all_numbers: List[str] = [seq.accession_number for seq in sequence_instances]
    new_data = retrieve_gb(all_numbers)
    
    seq : NuccoreSequence
    # make a dictionary mapping genbank accession -> genbank entry
    an_to_gb : Dict[str, Dict[str, str]] = {}
    update_summary: SequenceUpdateSummary = SequenceUpdateSummary()
    for genbank_data in new_data:
        an_to_gb[genbank_data['accession_number']] = genbank_data

    new_created_time = datetime.now(timezone.utc)
    genbank_dict: Dict[str, Any]
    instance: NuccoreSequence

    # using the fetched data as a base, add updated values for 'created' and ensure that the id is present
    fields_to_update = list(new_data[0].keys())
    fields_to_update.append('created')
    to_update: List[NuccoreSequence] = []
    to_delete: List[NuccoreSequence] = []
    for instance in sequence_instances:
        if instance.accession_number not in an_to_gb: # genbank data missing accession
            update_summary.deleted.append(instance.accession_number)
            to_delete.append(instance)
        else:
            genbank_dict = an_to_gb[instance.accession_number]
            instance.created = new_created_time
            has_any_field_changed: bool = False
            has_version_changed: bool = instance.version != genbank_dict['version']

            for field in fields_to_update:
                if not has_any_field_changed and genbank_dict[field] != getattr(instance, field):
                    has_any_field_changed = True 
                setattr(instance, field, genbank_dict[field])
            
            if has_version_changed:
                update_summary.accession_version_changed.append(instance.accession_number)
            elif has_any_field_changed:
                update_summary.metadata_changed.append(instance.accession_number)
            else:
                update_summary.no_change.append(instance.accession_number)
            to_update.append(instance)

    NuccoreSequence.objects.bulk_update(to_update, fields=fields_to_update, batch_size=100) 
    for delete_seq in to_delete:
        delete_seq.delete() 
    return update_summary     

class AccessionsAlreadyExist(BaseException): 
    '''
    A set of accession numbers already exist in a database.
    '''
    accession_numbers: List[str]
    def __init__(self, accession_numbers: List[str]) -> None:
        self.accession_numbers = accession_numbers

class AccessionsNotFound(BaseException):
    '''
    A set of accession numbers could not be located in the database.
    '''
    accession_numbers: List[str]
    def __init__(self, accession_numbers: List[str]) -> None:
        self.accession_numbers = accession_numbers

class DatabaseLocked(BaseException): ...

def delete_sequences_in_database(database: BlastDb, desired_nums: List[str]) -> int:
    '''
    Remove all sequences.

    Returns the number of sequence objects deleted.

    Raises
        DatabaseLocked: If database is locked for editing.
    '''
    if database.locked:
        raise DatabaseLocked()
    desired_nums = list(set(desired_nums))
    to_delete: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=database, accession_number__in=desired_nums)
    result = to_delete.delete()

    # Add deleted sequences to history
    log_deleted_sequences([s for s in to_delete], database=database)
    save_blastdb(database, perform_lock=False)

    return result[1]['barcode_blastn.NuccoreSequence']

def update_sequences_in_database(database: BlastDb, desired_numbers: List[str]) -> List[NuccoreSequence]:
    ''''
    Bulk update the given accession numbers (`desired_numbers`) in the database and return the resulting list of NuccoreSequences. 
    If `desired_numbers` empty, update all accessions in the database.

    Returns a tuple of two lists. The first list contains the saved NuccoreSequence objects, as returned by `.bulk_create()`. The second list contains the updated objects, as returned by `.bulk_update()`.

    Raises:
        DatabaseLocked: If database is locked for editing.
        AccessionsNotFound: If a given accession number in `desired_numbers` is not present in the database.
        AccessionLimitExceeded: If the number of accessions to update exceeds the maximum allowed
        GenbankConnectionError: Could not connect to GenBank or the request sent was bad
        InsufficientAccessionData: If all accession numbers could not be identified by GenBank 
    '''
    if database.locked:
        raise DatabaseLocked()
    desired_numbers = list(set(desired_numbers))
    # Check what accession numbers are duplicate (i.e. already existing in the datab)
    if len(desired_numbers) > 0:
        existing: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=database, accession_number__in=desired_numbers) # type: ignore        
        # raise error if not all numbers exist
        if existing.count() < len(desired_numbers):
            e: NuccoreSequence
            existing_nums: List[str] = [e.accession_number for e in existing]
            raise AccessionsNotFound([d for d in desired_numbers if d not in existing_nums])
    else:
        existing: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=database) # type: ignore        

    # retrieve data
    genbank_data = retrieve_gb(accession_numbers=desired_numbers)
    keys = [d['accession_number'] for d in genbank_data]
    # map accession numbers -> retrieved data dictionary
    acc_to_data: Dict[str, Dict[str, str]] = dict(zip(keys, genbank_data))

    # save the new sequences using a bulk operation
    to_update: List[NuccoreSequence] = []
    record: NuccoreSequence
    for record in existing:
        for key, value in acc_to_data[record.accession_number].items():
            setattr(record, key, str(value))
        record.updated = datetime.now()
        to_update.append(record)

    fields_to_update = list(genbank_data[0].keys())
    fields_to_update.append('updated')
    NuccoreSequence.objects.bulk_update(existing, fields=fields_to_update)
    return to_update


def filter_sequences_in_database(database: BlastDb, min_length: int = -1, max_length: int = -1, max_ambiguous_bases = -1, blacklist: List[str] = [], require_taxonomy: bool = False) -> List[NuccoreSequence]:
    '''
    Filter out sequences in a database that violate the provided parameters by deleting those sequences and returning a list of
    deletions.

    This function also calls `.save()` on the database to log the
    deletions in history, regardless of whether any deletions 
    occurred.
    '''
    objects: QuerySet[NuccoreSequence] = NuccoreSequence.objects.filter(owner_database=database)
    change_reason = []
    if min_length > -1 or max_length > -1:
        objects = objects.annotate(length=Length('dna_sequence'))
    violations: QuerySet[NuccoreSequence] = objects.filter(Q(accession_number__in=blacklist) | Q(version__in=blacklist))
    if len(blacklist) > 0:
        change_reason.append(f'Remove using blacklist {", ".join(blacklist)}')
    if min_length > -1:
        violations |= objects.filter(length__lt=min_length)
        change_reason.append(f'Delete length < {min_length} bp')
    if max_length > -1:
        violations |= objects.filter(length__gt=max_length)
        change_reason.append(f'Delete length > {max_length} bp')
    if max_ambiguous_bases > -1:
        violations |= objects.annotate(uppercase=Upper('dna_sequence'), ambiguous=Length('uppercase') - Length(Replace('uppercase', Value('N')))).filter(ambiguous__gt=max_ambiguous_bases)
        change_reason.append(f'Delete if Ns > {max_ambiguous_bases} bp')
    
    to_delete = [v.version for v in violations]
    violations.delete()
    append_change_reason(database, '\n'.join(change_reason))
    # database.deleted = ', '.join(to_delete)
    database.save()
    return violations

def add_sequences_to_database(database: BlastDb, desired_numbers: List[str] = [], search_term: Optional[str] = None, min_length: int = -1, max_length: int = -1, max_ambiguous_bases = -1, blacklist: List[str] = [], require_taxonomy: bool = False) -> List[NuccoreSequence]:
    '''
    Add a list of accession numbers to an existing database by bulk creating and return the resulting list of NuccoreSequences. Also log the sequences
    in the history and call save_blastdb()

    Returns a list of saved NuccoreSequence objects, as returned by `.bulk_create()`. 

    Raises:
        DatabaseLocked: If database is locked for editing.
        AccessionsAlreadyExist: If an accession specified already exists in the database
        AccessionLimitExceeded: If the number of accessions to add exceeds the maximum allowed
        GenbankConnectionError: Could not connect to GenBank or the request sent was bad
        InsufficientAccessionData: If all accession numbers could not be identified by GenBank 
    '''
    if database.locked:
        raise DatabaseLocked()
    if len(desired_numbers) == 0 and search_term is None:
        return []
    desired_numbers = list(set(desired_numbers))
    # Retrieve what accession numbers are already existing in the database
    existing: Set[str] = set(NuccoreSequence.objects.distinct().filter(owner_database=database).values_list('accession_number', flat=True))
    # raise error if any the numbers to be added already exist
    conflicts = [e for e in desired_numbers if e in existing]
    if len(conflicts) > 0:
        raise AccessionsAlreadyExist(conflicts)

    # retrieve data
    genbank_data = retrieve_gb(accession_numbers=desired_numbers, term=search_term)
    genbank_data = save_taxonomy(genbank_data)
    
    # save the new sequences using a bulk operation
    to_create: List[NuccoreSequence] = []
    data: Dict[str, Any]
    for data in genbank_data:
        try:
            an = data.get('accession_number')
            version = data.get('version')
        except KeyError:
            continue
        an = str(an)
        version = str(version)

        if an in existing:
            continue 
        
        # filter by sequence length
        sequence = data['dna_sequence']
        seq_len = len(sequence)
        if min_length > -1 and seq_len < min_length:
            continue 
        if max_length > -1 and seq_len > max_length:
            continue
        
        # filter by ambiguous bases
        n = len([base for base in sequence if base == 'N'])
        if max_ambiguous_bases > -1 and n > max_ambiguous_bases:
            continue 
    
        # check if taxonomy missing
        if require_taxonomy:
            taxa = ['superkingdom', 'kingdom', 'phylum', 'class', 'order', 'family', 'genus', 'species']
            missing = [t for t in taxa if data.get(f'taxon_{t}') is None]
            if len(missing) > 0:
                continue 
        
        # check if in blacklist
        if an in blacklist or version in blacklist:
            continue

        if an not in existing:
            new_seq = NuccoreSequence(owner_database=database, **data)
            to_create.append(new_seq)
            existing.add(an)

    # Add the initial accessions and search terms to be stored in the 
    # historical log of the database
    created_sequences = []
    created_sequences = NuccoreSequence.objects.bulk_create(to_create)

    log_added_sequences(instances=created_sequences, search_term=search_term, database=database)
    save_blastdb(database, perform_lock=False)
    return created_sequences

def save_sequence(obj: NuccoreSequence, commit: bool = False, raise_if_missing: bool = False, raise_errors: bool = True):
    '''
    Save an accession by fetching data from GenBank again and populating the instance. Used for both saving and updating an accession.
    Obj should already have id, accession_number and owner_database values.
    If commit is set to True, also save the instance. If set to False, you will need to call save manually after this function.
    If raise_errors is set to False, no errors will be raised. Default: True.

    Raises:
        ValueError does not have accession_number and owner_database values.

        AccessionsAlreadyExist: If change is False, and an existing accession with the same accession number already exists in the same database.

        AssertionError: If sequence claims to be editing an existing obj but that obj does not exist yet in the database

        GenBankConnectionError: Error connecting to GenBank.

        InsufficientAccessionData: If data from GenBank is insufficient for populating the fields.
    '''
    value_error: Optional[ValueError] = None
    if not obj.accession_number or len(obj.accession_number) == 0:
        value_error = ValueError('Missing accession numbers')
    elif not obj.owner_database:
        value_error = ValueError()
    elif obj.owner_database.locked:
        value_error = ValueError('Database locked')
    if raise_errors and not value_error is None:
        raise value_error
    else:
        print(f'WARN: Suppressed error {value_error}')

    accession_number = obj.accession_number

    # check if there is a duplicate
    try:
        existing = NuccoreSequence.objects.filter(owner_database=obj.owner_database).exclude(id=obj.id).get(accession_number=accession_number)
    except NuccoreSequence.DoesNotExist:
        pass
    else:
        # raise error if duplicate accession already exists
        error = AccessionsAlreadyExist([existing.accession_number])
        if raise_errors:
            raise error 
        else:
            print(f'WARN: Suppressed error {error}')

    # fetch GenBank data
    currentData: Dict[str, Any]
    try:
        currentData = retrieve_gb(accession_numbers=[accession_number],
                                  raise_if_missing=raise_if_missing)[0]
    except (GenBankConnectionError, InsufficientAccessionData, BaseException) as exc:
        if not raise_errors:
            print(f'WARN: Suppressed error {exc}')
            currentData = {}
        else:
            raise exc

    # check that the GenBank data is valid
    try:
        currentData = save_taxonomy([currentData])[0]
        taxon_fields = ['taxon_species', 'taxon_genus', 'taxon_family', 'taxon_order', 'taxon_class', 'taxon_phylum', 'taxon_kingdom', 'taxon_superkingdom']
        for taxon_field in taxon_fields:
            setattr(obj, taxon_field, currentData[taxon_field])
        serializer = NuccoreSequenceSerializer(obj, data=currentData, partial=True)
        if serializer.is_valid():
            if commit:
                # only save if commit specified
                return serializer.save()
            else:
                # only update instance key values and don't save
                for key, value in currentData.items():
                    setattr(obj, key, str(value))
                return obj
        else:
            raise AssertionError(serializer.errors)
    except BaseException as exc:
        if raise_errors:
            raise exc
        else:
            print(f'WARN: Suppressed error {exc}')
            return obj
    
def append_change_reason(database: BlastDb, reason: str):
    '''
    Modify the ._change_reason of database by appending the
    reason string provided.

    This function does not call .save(), so you must call .save() on the
    returned instances in order to save the ._change_reason to history. 
    '''
    change_reason = getattr(database, '_change_reason', '')
    if len(change_reason) > 0:
        reasons = [change_reason, reason]
    else:
        reasons = [reason]
    setattr(database, '_change_reason', ', '.join(reasons))
            
    return


def log_deleted_sequences(instances: List[NuccoreSequence], database: BlastDb) -> None:
    '''
    Modify the ._change_reason and .deleted of database to reflect
    the deletion of sequences given in instances. If the object database 
    already has a ._change_reason, append any newly added reasons to the 
    existing string.

    This function does not call .save(), so you must call .save() on the
    returned instances in order to save the ._change_reason to history. 

    Raises:

        ValueError: if any of the deleted sequences do not belong to the database
        provided.
    '''
    db_id = str(database.id)
    mismatches = [str(i) != db_id for i in instances]
    if any(mismatches):
        raise ValueError('One of the provided instances does not belong to the \
                         database specified')
    append_change_reason(database=database, reason='Deleted sequences')
    setattr(database, 'deleted', ', '.join([i.version for i in instances]))

def log_added_sequences(instances: List[NuccoreSequence], search_term: Optional[str], database: BlastDb) -> None:
    '''
    Modify the ._change_reason, .added, and .search_terms of database to reflect the addition of 
    sequences in instances. If the object database already has a ._change_reason,
    append any newly added reasons to the existing string.

    This function does not call .save(), so you must call .save() on the
    returned instances in order to save the changed data.

    Raises:

        ValueError: if any of the deleted sequences do not belong to the database
        provided.
    '''
    db_id = str(database.id)
    mismatches = [str(i) != db_id for i in instances]
    if any(mismatches):
        raise ValueError('One of the provided instances does not belong to the \
                         database specified')
    append_change_reason(database=database, reason='Added sequences')
    search_term = search_term if not search_term is None else ''
    setattr(database, 'added', ', '.join([i.version for i in instances]))
    setattr(database, 'search_terms', search_term)
    

        
        
    
