<?php

namespace App\Support;

class ErrorCategory
{
    public const NONE = 'NONE';
    public const VALIDATION_ERROR = 'VALIDATION_ERROR';
    public const DATABASE_ERROR = 'DATABASE_ERROR';
    public const TIMEOUT_ERROR = 'TIMEOUT_ERROR';
    public const SYSTEM_ERROR = 'SYSTEM_ERROR';
    public const UNKNOWN = 'UNKNOWN';
    public const TIMEOUT_THRESHOLD_MS = 4000;
}
