<?php
return [
    'general' => [
        'ports' => [
            22,
            53,
            80,
            443,
            179,
            2055,
            4739,
            6343,
        ],
        'sources' => [
            'netflow',
            'ipfix',
            'sflow',
        ],
        'filters' => [
            'proto tcp',
            'proto udp',
            'src net 2a0c:b641:b50::/44 or dst net 2a0c:b641:b50::/44',
            'dst port 80 or dst port 443',
            'dst port 179 or src port 179',
        ],
        'db' => getenv('NFSEN_DATASOURCE') ?: 'RRD',
        'processor' => 'NfDump',
        'max_stats_window' => (int) (getenv('NFSEN_MAX_STATS_WINDOW') ?: 604800),
        'netbox_url' => (string) (getenv('NFSEN_NETBOX_URL') ?: ''),
        'netbox_token' => (string) (getenv('NFSEN_NETBOX_TOKEN') ?: ''),
    ],
    'frontend' => [
        'reload_interval' => 30,
        'defaults' => [
            'view' => 'graphs',
            'graphs' => [
                'display' => 'sources',
                'datatype' => 'traffic',
                'protocols' => ['any'],
            ],
            'flows' => [
                'limit' => 100,
            ],
            'statistics' => [
                'order_by' => 'bytes',
            ],
        ],
    ],
    'nfdump' => [
        'binary' => getenv('NFSEN_NFDUMP_BINARY') ?: '/usr/bin/nfdump',
        'profiles-data' => getenv('NFSEN_NFDUMP_PROFILES') ?: '/var/nfdump/profiles-data',
        'profile' => getenv('NFSEN_NFDUMP_PROFILE') ?: 'live',
        'max-processes' => (int) (getenv('NFSEN_NFDUMP_MAX_PROCESSES') ?: 1),
    ],
    'db' => [
        'RRD' => [
            'data_path' => getenv('NFSEN_RRD_PATH') ?: null,
            'import_years' => (int) (getenv('NFSEN_IMPORT_YEARS') ?: 1),
        ],
    ],
    'log' => [
        'priority' => \LOG_INFO,
    ],
];
