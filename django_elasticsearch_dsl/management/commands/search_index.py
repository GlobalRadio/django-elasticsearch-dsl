from __future__ import unicode_literals, absolute_import
from django.core.management.base import BaseCommand, CommandError
from django.utils.six.moves import input
from django.utils import timezone
from ...registries import registry


class Command(BaseCommand):
    help = 'Manage elasticsearch index.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--models',
            metavar='app[.model]',
            type=str,
            nargs='*',
            help="Specify the model or app to be updated in elasticsearch"
        )
        parser.add_argument(
            '--create',
            action='store_const',
            dest='action',
            const='create',
            help="Create the indices in elasticsearch"
        )
        parser.add_argument(
            '--populate',
            action='store_const',
            dest='action',
            const='populate',
            help="Populate elasticsearch indices with models data"
        )
        parser.add_argument(
            '--delete',
            action='store_const',
            dest='action',
            const='delete',
            help="Delete the indices in elasticsearch"
        )
        parser.add_argument(
            '--rebuild',
            action='store_const',
            dest='action',
            const='rebuild',
            help="Delete the indices and then recreate and populate them"
        )
        parser.add_argument(
            '--reindex',
            action='store_const',
            dest='action',
            const='reindex',
            help="Rebuilds indices with no downtime using an alias"
        )
        parser.add_argument(
            '-f',
            action='store_true',
            dest='force',
            help="Force operations without asking"
        )

    def _get_models(self, args):
        """
        Get Models from registry that match the --models args
        """
        if args:
            models = []
            for arg in args:
                arg = arg.lower()
                match_found = False

                for model in registry.get_models():
                    if model._meta.app_label == arg:
                        models.append(model)
                        match_found = True
                    elif '{}.{}'.format(
                        model._meta.app_label.lower(),
                        model._meta.model_name.lower()
                    ) == arg:
                        models.append(model)
                        match_found = True

                if not match_found:
                    raise CommandError("No model or app named {}".format(arg))
        else:
            models = registry.get_models()

        return set(models)

    def _create(self, models, options):
        for index in registry.get_indices(models):
            self.stdout.write("Creating index '{}'".format(index))
            index.create()

    def _populate(self, models, options):
        for doc in registry.get_documents(models):
            qs = doc().get_queryset()
            self.stdout.write("Indexing {} '{}' objects".format(
                qs.count(), doc._doc_type.model.__name__)
            )
            doc().update(qs)

    def _delete(self, models, options):
        index_names = [str(index) for index in registry.get_indices(models)]

        if not options['force']:
            response = input(
                "Are you sure you want to delete "
                "the '{}' indexes? [n/Y]: ".format(", ".join(index_names)))
            if response.lower() != 'y':
                self.stdout.write('Aborted')
                return False

        for index in registry.get_indices(models):
            self.stdout.write("Deleting index '{}'".format(index))
            index.delete(ignore=404)
        return True

    def _rebuild(self, models, options):
        if not self._delete(models, options):
            return
        self._create(models, options)
        self._populate(models, options)

    def _reindex(self, models, options):
        for cls in registry.get_documents(models):
            doc = cls()
            es = doc.connection

            alias = doc._doc_type.index

            next_index = self._next_index_name(alias)
            es.indices.create(next_index)
            self.stdout.write("Creating index '{}'".format(next_index))

            qs = doc.get_queryset()
            self.stdout.write("Indexing {} '{}' objects".format(
                qs.count(), cls._doc_type.model.__name__)
            )
            doc.update(qs, index=next_index)
            es.indices.refresh(index=next_index)

            self.stdout.write("Updating alias '{}' -> '{}'".format(alias, next_index))
            es.indices.put_alias(next_index, alias)
            es.indices.update_aliases(body={
                'actions': [
                    {'remove': {'alias': alias, 'index': '{}-*'.format(alias)}},
                    {'add': {'alias': alias, 'index': next_index}},
                ]
            })

            old_indices = [index for index in es.indices.get("{}-*".format(alias)) if index != next_index]
            for old_index in old_indices:
                es.indices.delete(old_index)

    def _next_index_name(self, name):
        return '{}-{}'.format(name, timezone.now().strftime('%Y.%m.%d.%H.%M.%S'))

    def handle(self, *args, **options):
        if not options['action']:
            raise CommandError(
                "No action specified. Must be one of"
                " '--create','--populate', '--delete' or '--rebuild' ."
            )

        action = options['action']
        models = self._get_models(options['models'])

        if action == 'create':
            self._create(models, options)
        elif action == 'populate':
            self._populate(models, options)
        elif action == 'delete':
            self._delete(models, options)
        elif action == 'rebuild':
            self._rebuild(models, options)
        elif action == 'reindex':
            self._reindex(models, options)
        else:
            raise CommandError(
                "Invalid action. Must be one of"
                " '--create','--populate', '--delete', '--rebuild' or '--reindex' ."
            )
